from rag.rule_store import RuleStore

store = RuleStore()
results = store.query("SHoud i  sell HDFC Strangle for July ", n_results=4)
for r in results:
    print(r["rule_id"], r["score"], r["name"])