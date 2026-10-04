"""The Modal app that folds sequences on a GPU, so nothing here runs locally."""

from string import ascii_uppercase

import modal

# The single-sequence model: no MSA, so far cheaper. It folds one protein chain,
# so a complex needs the full ESMFold2, which conditions chains on each other.
MONOMER_MODEL = "biohub/ESMFold2-Fast"
COMPLEX_MODEL = "biohub/ESMFold2"
GPU = "L40S"  # 48 GB, the cheapest card that fits these models.

# The weights are several GB, so keep the Hugging Face cache on a volume and pay for
# the download once rather than on every cold start.
cache = modal.Volume.from_name("esmfold2-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    # The container imports this module, so it imports the package around it too:
    # pydantic is what the configs and entities are built on.
    .pip_install("esm", "torch", "pydantic", "huggingface_hub[hf_transfer]")
    .env({"HF_HOME": "/cache/huggingface", "HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

app = modal.App("node-dag-esmfold2")


def _chain_ids(n: int) -> list[str]:
    """``n`` chain ids in letter order: A to Z, then AA, AB and so on."""
    ids = []
    for i in range(n):
        q, r = divmod(i, 26)
        ids.append(
            ascii_uppercase[r]
            if q == 0
            else ascii_uppercase[q - 1] + ascii_uppercase[r]
        )
    return ids


@app.function(gpu=GPU, image=image, volumes={"/cache": cache}, timeout=3600)
def fold(
    groups: list[list[str]],
    model_id: str,
    num_loops: int,
    num_sampling_steps: int,
    seed: int,
) -> list[str]:
    """Fold each group of protein chains as one structure and return its mmCIF.

    A group of one chain comes back as a monomer; several chains co-fold as a
    complex, lettered in group order. The whole list is folded in one call, so
    the weights are loaded once per run.
    """
    from esm.models.esmfold2 import (
        ESMFold2InputBuilder,
        EsmFold2Model,
        ProteinInput,
        StructurePredictionInput,
    )

    model = EsmFold2Model.from_pretrained(model_id, device="cuda").eval()
    # Without this the model runs its reference kernels, which are about 12x slower,
    # so the same fold costs about 12x as much.
    model.set_kernel_backend("fused")
    cache.commit()  # Keep the weights for the next cold start.

    builder = ESMFold2InputBuilder()
    folded = []
    for chains in groups:
        result = builder.fold(
            model,
            StructurePredictionInput(
                sequences=[
                    ProteinInput(id=cid, sequence=s)
                    for cid, s in zip(_chain_ids(len(chains)), chains, strict=True)
                ]
            ),
            num_loops=num_loops,
            num_sampling_steps=num_sampling_steps,
            num_diffusion_samples=1,
            seed=seed,
        )
        folded.append(result.complex.to_mmcif())
    return folded
