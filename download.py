import phoenix as px
from phoenix.trace.dsl import SpanQuery

client = px.Client()
trace_id = "9844f7603e7b1f08b16e5029f35eca52"

# 1. Ensure this matches your UI exactly
project_name = "legalkgent-graphrag" 

# 2. Build the query
query = SpanQuery().where(f"trace_id == '{trace_id}'")

# 3. Pass project_name directly into query_spans
# Note: You will still see the DeprecationWarning in the terminal. Ignore it!
spans_df = client.query_spans(query, project_name=project_name)

# 4. Export
if spans_df is not None and not spans_df.empty:
    output_file = f"trace_{trace_id[:8]}.json"
    spans_df.to_json(output_file, orient="records", indent=4)
    print(f"Success! Trace downloaded to {output_file}")
else:
    print(f"List is still empty. Double-check that '{project_name}' is exactly correct.")