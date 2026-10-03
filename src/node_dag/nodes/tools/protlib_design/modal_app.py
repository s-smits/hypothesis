"""The Modal app that scores mutations and solves each library on a GPU, so nothing here runs locally."""

import modal

GPU = "T4"  # ProteinMPNN and ESM-2-8M are small; 16 GB is comfortable.

# The PLM weights come from Hugging Face, so keep the cache on a volume and pay
# for the download once rather than on every cold start. ProteinMPNN's weights
# ship inside its package.
cache = modal.Volume.from_name("protlib-designer-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    # protlib-designer pulls torch for the scorers; the container imports this
    # module, so it imports the package around it too: pydantic is what the
    # configs and entities are built on.
    .pip_install("protlib-designer[all]", "pydantic")
    .env({"HF_HOME": "/cache/huggingface", "HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

app = modal.App("node-dag-protlib-design")


@app.function(gpu=GPU, image=image, volumes={"/cache": cache}, timeout=3600)
def design(payloads: list[dict], options: dict) -> list[list[str]]:
    """Return one library of solutions per payload.

    Each payload is ``{"sequence", "mmcif", "positions"}`` with explicit
    ``{WT}{chain}{index}`` positions; each returned solution is a comma-joined
    mutation string like ``"WA12C,YA34D"`` for the caller to apply.
    """
    import tempfile
    from functools import reduce
    from pathlib import Path

    import pandas as pd
    from protlib_designer.dataloader import DataLoader
    from protlib_designer.filter.no_filter import NoFilter
    from protlib_designer.generator.ilp_generator import ILPGenerator
    from protlib_designer.scorer.ifold_scorer import IFOLDScorer
    from protlib_designer.scorer.plm_scorer import PLMScorer
    from protlib_designer.solver.generate_and_remove_solver import (
        GenerateAndRemoveSolver,
    )
    from protlib_designer.utils import (
        cif_to_pdb,
        format_and_validate_protlib_designer_parameters,
    )

    # Load every model once for the whole batch, then reuse it per structure.
    ifold = (
        IFOLDScorer(seed=options["seed"], score_type="minus_llr")
        if options["use_ifold"]
        else None
    )
    plms = [
        PLMScorer(model_name=m, score_type="minus_llr", mask=True)
        for m in options["plm_models"]
    ]
    cache.commit()  # Keep the weights for the next cold start.

    libraries = []
    for payload in payloads:
        positions = payload["positions"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cif = tmp_path / "input.cif"
            cif.write_text(payload["mmcif"])
            pdb = tmp_path / "input.pdb"
            if ifold is not None:
                cif_to_pdb(str(cif), output_pdb=str(pdb))

            dataframes = []
            if ifold is not None:
                dataframes.append(ifold.get_scores(str(pdb), positions))
            for plm in plms:
                dataframes.append(
                    plm.get_scores(
                        payload["sequence"], positions, options["plm_chain_type"]
                    )
                )
            scores = reduce(
                lambda left, right: pd.merge(left, right, on="Mutation", how="left"),
                dataframes,
            )
            data = tmp_path / "scores.csv"
            scores.to_csv(data, index=False)

            config, _ = format_and_validate_protlib_designer_parameters(
                output_folder=str(tmp_path / "out"),
                data=str(data),
                min_mut=options["min_mut"],
                max_mut=options["max_mut"],
                nb_iterations=options["library_size"],
                forbidden_aa=",".join(options["forbidden_aa"]) or None,
                max_arom_per_seq=options["max_arom_per_seq"],
                dissimilarity_tolerance=options["dissimilarity_tolerance"],
                interleave_mutant_order=options["interleave_mutant_order"],
                force_mutant_order_balance=options["force_mutant_order_balance"],
                schedule=options["schedule"],
                schedule_param=",".join(map(str, options["schedule_param"])) or None,
                objective_constraints=None,
                objective_constraints_param=None,
                weighted_multi_objective=options["weighted_multi_objective"],
                debug=0,
                data_normalization=options["data_normalization"],
            )
            loader = DataLoader(str(data))
            loader.load_data()
            config = loader.update_config_with_data(config)

            generator = ILPGenerator(loader, config)
            solver = GenerateAndRemoveSolver(
                generator,
                NoFilter(),
                length_of_library=options["library_size"],
                maximum_number_of_iterations=2 * options["library_size"],
            )
            solver.run()
            libraries.append([s["solution"] for s in solver.list_of_solution_dicts])
    return libraries
