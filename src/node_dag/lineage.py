"""Explicit instance-to-result mapping for sequence-generating steps.

``Entity.id`` hashes kind and sequence, and ``Table.of`` merges identical entities.
Both are deliberate, and neither is safe to use as a benchmark's denominator: two
candidates that recode to the same sequence become one row, and a candidate reachable
from two genes has no single parent. A benchmark that counts the rows a scorer saw
therefore has a denominator that shrinks with how convergent the candidates happen to
be, which flatters an algorithm for producing less variety.

This module keeps the mapping outside the entities. ``trace`` records which instance
each variant came from, ``Lineage`` keeps every variant including the duplicates, and
``outcomes`` reports one row per instance whether or not it produced a scored variant,
so an empty or failed instance stays in the denominator instead of vanishing from an
average.

It is a benchmark-side helper, not a node, and it changes no DAG contract.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from node_dag.nodes.base import BaseNode
from node_dag.types import Dna

__all__ = ["Lineage", "Outcome", "Variant", "outcomes", "trace"]


@dataclass(frozen=True)
class Variant:
    """One generated sequence, with the instance and position that produced it.

    Args:
        instance: The instance key its parent was filed under.
        index: Its position in that instance's variants, so duplicates stay distinct.
        entity: The generated sequence. Its ``id`` may equal another variant's.
    """

    instance: str
    index: int
    entity: Dna


@dataclass(frozen=True)
class Outcome:
    """What one instance produced, including the case where it produced nothing.

    Args:
        instance: The instance key.
        parent: The sequence the variants were generated from.
        values: The score of each of this instance's variants, in order, for the
            variants that carry a score. Shorter than ``n_variants`` when scores are
            missing, and empty when none were scored.
        n_variants: How many variants this instance generated, duplicates included.
        missing: Variants with no score in the column, by index.
    """

    instance: str
    parent: Dna
    values: tuple[float, ...]
    n_variants: int
    missing: tuple[int, ...]

    @property
    def scored(self) -> bool:
        """Whether any variant of this instance carries a score."""
        return bool(self.values)

    def best(self, *, higher_is_better: bool) -> float | None:
        """The best score of this instance, or None when nothing was scored.

        A caller must decide what an unscored instance means for its statistic rather
        than receiving a number that hides it.
        """
        if not self.values:
            return None
        return max(self.values) if higher_is_better else min(self.values)


@dataclass(frozen=True)
class Lineage:
    """Which instance each generated sequence came from.

    Args:
        parents: The input sequence of each instance key.
        variants: Every generated variant, duplicates included, in generation order.
    """

    parents: Mapping[str, Dna]
    variants: tuple[Variant, ...]

    @property
    def denominator(self) -> int:
        """The number of instances, which no amount of merging changes."""
        return len(self.parents)

    def by_instance(self) -> dict[str, list[Variant]]:
        """The variants of each instance, in order, for every instance.

        An instance that generated nothing maps to an empty list rather than being
        left out.
        """
        out: dict[str, list[Variant]] = {k: [] for k in self.parents}
        for v in self.variants:
            out[v.instance].append(v)
        return out

    def collisions(self) -> dict[str, set[str]]:
        """Entity ids reachable from more than one instance, with those instances.

        A non-empty result means parentage cannot be recovered from the sequence, so
        per-instance reporting has to use this mapping rather than the entity id.
        """
        seen: dict[str, set[str]] = defaultdict(set)
        for v in self.variants:
            seen[v.entity.id].add(v.instance)
        return {i: ks for i, ks in seen.items() if len(ks) > 1}

    def lost_to_merging(self) -> int:
        """How many variants a table of these entities would merge away.

        The count a benchmark would silently lose by taking its denominator from the
        scored table instead of from the instances.
        """
        return len(self.variants) - len({v.entity.id for v in self.variants})


def trace(parents: Mapping[str, Dna], node: BaseNode) -> Lineage:
    """Run ``node`` on each instance separately and record what each one produced.

    Running per instance is what makes parentage unambiguous. It gives the same
    sequences as one batched call for a node whose randomness is seeded by
    configuration and sequence id, as ``mutate_synonymous`` and ``recode_targeted``
    are; a node that seeds from batch position would differ, and its results should
    not be compared across batches anyway.

    Args:
        parents: The input sequence of each instance key.
        node: A built tool node taking ``sequence`` and returning ``Dna``.
    """
    variants: list[Variant] = []
    for key, parent in parents.items():
        produced = node.run(sequence=[parent])
        variants.extend(
            Variant(instance=key, index=i, entity=e) for i, e in enumerate(produced)
        )
    return Lineage(parents=dict(parents), variants=tuple(variants))


def outcomes(lineage: Lineage, scores: Mapping[str, float]) -> tuple[Outcome, ...]:
    """One ``Outcome`` per instance, in ``lineage.parents`` order.

    Args:
        lineage: The recorded mapping.
        scores: One score column of a table, mapping entity id to value, as
            ``Table.scores[column]`` gives it.
    """
    by_instance = lineage.by_instance()
    return tuple(
        Outcome(
            instance=key,
            parent=parent,
            values=tuple(
                scores[v.entity.id] for v in by_instance[key] if v.entity.id in scores
            ),
            n_variants=len(by_instance[key]),
            missing=tuple(
                v.index for v in by_instance[key] if v.entity.id not in scores
            ),
        )
        for key, parent in lineage.parents.items()
    )
