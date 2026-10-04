What differs between the archived runs and the builder this skill is for: #12 with #19 and
main (#25, #27, #28, #30) merged in. A rule must not teach, rely on, or work around any of it,
and a failure that one of these changes fixed or caused is not a lesson for the skill.

Not in that builder (the stack above it, from the diffs of #14, #15, #17 and #20):

- The plan has no `result_source`. A plan names no single set its criteria are about, and the
  check for it (each criterion asserted on that step, or on a step that reads it with no tool
  between, and the set holding entities) is not made. #20.
- A plan that reruns earlier wiring is refused whatever its assertions say. #20 lets the same
  wiring through when it keeps every earlier assertion no weaker and strengthens one, with `yes`
  where it was `produced`. Do not advise "strengthen the assertion and run it again".
- `domesticate`, `gc_target_recode` and `codon_optimise` may still swap a stop codon for another
  stop. #14 and #20 stop them. `constraint_check` reports four scores and has no `immutable`
  config or `immutable_unchanged` score. Do not write rules that steer around a stop codon
  changing or check codons by position.
- The benchmark gate does not enforce immutable codons, and a failed instance is not counted as
  zero in its mean. #14, #17. Benchmark goals have no executable constraint checks.
- An attempt's transcript is kept only when the activity returned, and a stalled model call is
  not named as the silence limit. #15.

In that builder, and absent from most archived runs:

- A node cannot be requested. Requests are off (#30), and with them off the builder is shown no
  `requests` field, is told to look for a conversion as a chain of nodes, and its critic cannot
  name `missing_tool`. A run that blocked on a requested node, or a critique that said a tool was
  missing, is not a lesson: write nothing that tells the builder to request a node.
- `create_node` accepts its config as an object or a JSON string (#19). Do not write a rule about
  how to format the config argument.
- `mutate_synonymous` and `resample_synonymous` keep a stop codon as it is (#19).
- There is a `trim_to_first_start` node (#27).
- A goal must give its entities. Nothing is fetched from NCBI, and an input may be any entity
  kind (#28). Do not write rules about finding or fetching a gene.
- A run defaults to 20 rounds (#25).

Not new, and so open to a rule: the nodes in `src/node_dag/nodes/tools`, the filters,
`beats_reference` and `top_k`, the assertion branches `produced`, `yes` and `no`, the guards on
a plan, and the verifier and critique stages as they ran.
