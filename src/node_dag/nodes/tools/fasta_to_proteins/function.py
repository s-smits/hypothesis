import io

from Bio import SeqIO
from pydantic import ValidationError

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.fasta_to_proteins.config import FastaToProteinsConfig
from node_dag.types import AminoAcidSequence, FastaFile


class FastaToProteins(BaseNode[FastaToProteinsConfig]):
    """Read each FASTA file's records as amino acid sequences."""

    def run(self, file: list[FastaFile]) -> list[AminoAcidSequence]:
        """Return one amino acid sequence per record, headers dropped."""
        out = []
        for f in file:
            records = list(SeqIO.parse(io.StringIO(f.sequence), "fasta"))
            if not records:
                raise ValueError(f"{f.name or 'The FASTA file'} holds no records.")
            where = f" in {f.name}" if f.name else ""
            for r in records:
                if not r.seq:
                    raise ValueError(f"Record {r.id!r}{where} holds no sequence.")
                try:
                    out.append(AminoAcidSequence(sequence=str(r.seq).upper()))
                except ValidationError as e:
                    raise ValueError(
                        f"Record {r.id!r}{where} is not amino acids: "
                        f"{e.errors()[0]['msg']}"
                    ) from e
        return out
