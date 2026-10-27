Resources have a stable **RID** of the form `ri.emithran.main.<type>.<key>`:

| Type | Example |
|---|---|
| material | `ri.emithran.main.material.248` |
| dataset | `ri.emithran.main.dataset.42` |
| category | `ri.emithran.main.category.11` |
| observation | `ri.emithran.main.observation.1203` |
| source | `ri.emithran.main.source.frankfurter-fx` |
| build | `ri.emithran.main.build.12` |

Endpoints that take a RID also accept the bare key (`248`). A RID of the wrong type returns
`400 INVALID_ARGUMENT / InvalidRid`. Ontology objects additionally expose `__primaryKey`, which is the value used in
[Get Object](/docs/ontologies/ontology-objects/get-object).
