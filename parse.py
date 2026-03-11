import json
from datetime import datetime

def print_dynamic_trace_flow(file_path, truncate_len=300):
    """
    Parses an Arize Phoenix JSON trace and prints the chronological execution flow.
    Automatically detects if a span is an Agent, Tool, or LLM and extracts I/O.
    """
    with open(file_path, 'r') as f:
        spans = json.load(f)

    # 1. Sort spans chronologically by start_time to get the true execution order
    spans.sort(key=lambda x: x.get('start_time', 0))

    print(f"\n{'='*60}")
    print(f" TRACE EXECUTION TIMELINE ({len(spans)} Steps)")
    print(f"{'='*60}\n")

    for i, span in enumerate(spans):
        name = span.get('name', 'Unknown')
        span_kind = span.get('span_kind', 'UNKNOWN')
        
        # Calculate execution duration
        start = span.get('start_time', 0)
        end = span.get('end_time', 0)
        duration_ms = end - start
        
        print(f"[{i+1}] STEP: {name.upper()} | Type: {span_kind} | Duration: {duration_ms}ms")

        # ---------------------------------------------------------
        # SCENARIO A: Root Agent / Main QA Node
        # ---------------------------------------------------------
        if 'attributes.question' in span and span.get('attributes.question'):
            print(f"    -> INPUT (User Question):\n       {span.get('attributes.question')}")
            print(f"    <- OUTPUT (Final Answer):\n       {span.get('attributes.answer')}")

        # ---------------------------------------------------------
        # SCENARIO B: LLM Calls (Mistral, OpenAI, etc.)
        # ---------------------------------------------------------
        elif span_kind == 'LLM' or 'chat' in name.lower():
            
            # Dynamically extract LLM Inputs
            in_msgs = span.get('attributes.llm.input_messages')
            if in_msgs:
                print("    -> INPUT (LLM Messages):")
                for msg in in_msgs:
                    role = msg.get('message.role', 'unknown').upper()
                    content = msg.get('message.content', '')
                    # Truncate long system prompts for terminal readability
                    clean_content = content.replace('\n', ' ')
                    preview = clean_content[:truncate_len] + "..." if len(clean_content) > truncate_len else clean_content
                    print(f"       [{role}]: {preview}")
            
            # Dynamically extract LLM Outputs
            out_msgs = span.get('attributes.llm.output_messages')
            if out_msgs:
                print("    <- OUTPUT (LLM Response):")
                for msg in out_msgs:
                    role = msg.get('message.role', 'unknown').upper()
                    content = msg.get('message.content', '')
                    clean_content = content.replace('\n', ' ')
                    preview = clean_content[:truncate_len] + "..." if len(clean_content) > truncate_len else clean_content
                    print(f"       [{role}]: {preview}")
            elif span.get('attributes.output.value'):
                out_val = str(span.get('attributes.output.value')).replace('\n', ' ')
                print(f"    <- OUTPUT (Raw): {out_val[:truncate_len]}...")

        # ---------------------------------------------------------
        # SCENARIO C: Tools (Semantic Search, Cypher, API calls)
        # ---------------------------------------------------------
        else:
            # Dynamically search for common input keys
            tool_input = (
                span.get('attributes.cypher_query') or 
                span.get('attributes.query') or 
                span.get('attributes.input.value')
            )
            
            # Dynamically search for common output keys
            tool_output = (
                span.get('attributes.result_preview') or 
                span.get('attributes.output.value')
            )

            if tool_input:
                clean_in = str(tool_input).replace('\n', ' ')
                print(f"    -> INPUT (Tool Parameters):\n       {clean_in}")
            
            if tool_output:
                clean_out = str(tool_output).replace('\n', ' ')
                preview = clean_out[:truncate_len] + "..." if len(clean_out) > truncate_len else clean_out
                print(f"    <- OUTPUT (Tool Result):\n       {preview}")
        
        print("-" * 60)

# Run the script
print(print_dynamic_trace_flow('trace_9844f760.json', truncate_len=5000))