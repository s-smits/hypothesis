"""The Modal app that folds sequences on a GPU, so nothing here runs locally."""

import modal

MODEL = "biohub/ESMFold2-Fast"  # The single-sequence model: no MSA, so far cheaper.
GPU = "L40S"  # 48 GB, enough for the 7B model, and the cheapest card that fits it.

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


@app.function(gpu=GPU, image=image, volumes={"/cache": cache}, timeout=3600)
def fold(
    sequences: list[str], num_loops: int, num_sampling_steps: int, seed: int
) -> list[str]:
    """Fold each amino acid sequence as a monomer and return its mmCIF string.

    The whole list is folded in one call, so the weights are loaded once per run.
    """
    from esm.models.esmfold2 import (
        ESMFold2InputBuilder,
        EsmFold2Model,
        ProteinInput,
        StructurePredictionInput,
    )

    model = EsmFold2Model.from_pretrained(MODEL, device="cuda").eval()
    # Without this the model runs its reference kernels, which are about 12x slower,
    # so the same fold costs about 12x as much.
    model.set_kernel_backend("fused")
    cache.commit()  # Keep the weights for the next cold start.

    builder = ESMFold2InputBuilder()
    folded = []
    for s in sequences:
        # One protein chain and nothing else, so what comes back is a monomer.
        result = builder.fold(
            model,
            StructurePredictionInput(sequences=[ProteinInput(id="A", sequence=s)]),
            num_loops=num_loops,
            num_sampling_steps=num_sampling_steps,
            num_diffusion_samples=1,
            seed=seed,
        )
        folded.append(result.complex.to_mmcif())
    return folded
