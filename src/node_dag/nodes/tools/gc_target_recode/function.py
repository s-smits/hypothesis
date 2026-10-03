from itertools import accumulate

from node_dag.dna import CODON_TABLE, SYNONYMS, codons, gc_window_fractions
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.gc_target_recode.config import GcTargetRecodeConfig
from node_dag.types import Dna

# (window GC fractions, their deviations, prefix maxima, suffix maxima, total).
type _State = tuple[list[float], list[float], list[float], list[float], float]


class GcTargetRecode(BaseNode[GcTargetRecodeConfig]):
    """Greedy synonymous descent toward ``config.target`` windowed GC."""

    def run(self, sequence: list[Dna]) -> list[Dna]:
        """Return one recoded sequence per input."""
        return [self._recode(s) for s in sequence]

    def _recode(self, s: Dna) -> Dna:
        cs = codons(s.sequence)
        width = min(self.config.window, len(s.sequence))
        for _ in range(self.config.max_passes):
            if not cs or not self._sweep(cs, width):
                break
        return Dna(sequence="".join(cs))

    def _sweep(self, cs: list[str], width: int) -> bool:
        """Take every strictly improving codon swap, left to right."""
        improved = False
        state = self._stats("".join(cs), width)
        for i, old in enumerate(cs):
            if len(old) != 3 or len(SYNONYMS[CODON_TABLE[old]]) < 2:
                continue
            best, best_obj = old, self._objective(state, cs, i, old, width)
            for alt in SYNONYMS[CODON_TABLE[old]]:
                if (
                    alt != old
                    and (obj := self._objective(state, cs, i, alt, width)) < best_obj
                ):
                    best, best_obj = alt, obj
            if best != old:
                cs[i] = best
                improved = True
                state = self._stats("".join(cs), width)
        return improved

    def _stats(self, seq: str, width: int) -> _State:
        """Window deviations and the prefix and suffix maxima over them."""
        fracs = gc_window_fractions(seq, width)
        devs = [abs(f - self.config.target) for f in fracs]
        pre = list(accumulate(devs, max))
        suf = list(accumulate(reversed(devs), max))[::-1]
        return fracs, devs, pre, suf, sum(devs)

    def _objective(
        self,
        state: _State,
        cs: list[str],
        i: int,
        codon: str,
        width: int,
    ) -> tuple[float, float]:
        """The (worst, total) window deviation if codon ``i`` were ``codon``.

        Only the windows covering a base of codon ``i`` can change, so the rest
        come out of the prefix and suffix maxima instead of a rescan.
        """
        fracs, devs, pre, suf, total = state
        lo = max(0, 3 * i - width + 1)
        hi = min(3 * i + 2, len(devs) - 1)
        new = []
        for j in range(lo, hi + 1):
            delta = sum(
                (codon[b - 3 * i] in "GC") - (cs[i][b - 3 * i] in "GC")
                for b in range(max(3 * i, j), min(3 * i + 3, j + width))
            )
            new.append(abs(fracs[j] + delta / width - self.config.target))
        worst = max(
            max(new),
            pre[lo - 1] if lo else 0.0,
            suf[hi + 1] if hi + 1 < len(devs) else 0.0,
        )
        return worst, total - sum(devs[lo : hi + 1]) + sum(new)
