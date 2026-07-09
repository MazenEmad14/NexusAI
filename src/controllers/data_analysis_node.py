import json
import logging
from typing import Dict, Any
from models.agent_state import AgentState
from helpers.config import get_settings

logger = logging.getLogger("uvicorn.error")

async def data_analysis_node(state: AgentState) -> AgentState:
    query = state.get("query")
    file_path = state.get("file_path")

    if not file_path:
        state["result"] = "Error: No file_path provided for data analysis."
        state["status"] = "error"
        return state

    app_settings = get_settings()

    try:
        import pandas as pd
        df = pd.read_csv(file_path)
        data_preview = df.head(3).to_string()
        columns = ", ".join(df.columns)
        dtypes_info = df.dtypes.to_string()
    except Exception as e:
        logger.error(f"Failed to read CSV for prompt: {e}")
        data_preview = "Could not load data"
        columns = "Unknown"
        dtypes_info = "Unknown"

    system_prompt = f"""You are a data analyst assistant. You have a CSV file at {file_path}.
Columns available: {columns}
Data preview:
{data_preview}
Data types:
{dtypes_info}

User's query: "{query}"

Output ONLY raw Python code — no markdown, no ```, no explanations, no comments before or after the code.

The code must:

1. Load data with: df = pd.read_csv(r'{file_path}')
   IMPORTANT: If a column contains numbers but has a string/object dtype (e.g. contains commas like '10,000'), clean it before math operations: df['Col'] = pd.to_numeric(df['Col'].astype(str).str.replace(',', ''), errors='coerce')

2. Wrap the analysis logic in a try/except Exception as e: block. If an error occurs, set:
   result = {{
     "text_summary": f"حدث خطأ أثناء تحليل البيانات: {{str(e)}}",
     "chart_json": None
   }}

3. On success, build a variable named exactly `result` (a dict) with exactly two keys:
   - 'text_summary': short, natural, voice-friendly explanation of the finding.
     Rules: match the query's language (Arabic or English), no emojis, no repeating the question, no over-apologizing, round numbers to 2 decimals when spoken.
   - 'chart_json':
     * If the query needs a visual comparison/trend/distribution/correlation → a dict with 'type' ('bar'/'line'/'pie'/'scatter'), 'xAxisKey' (exact key name for X axis/category), and 'data' (list of dicts).
     * IMPORTANT FOR DATA: ALWAYS generate the `data` list dynamically using pandas, e.g. `df[['Age', 'Salary']].to_dict('records')`. NEVER hardcode the data points in the code!
     * If the query only needs a single number/fact with no meaningful chart → set 'chart_json': None.
     * If a categorical field has more than 10 unique values, aggregate the rest into an "Other" category or show only the top 10 by value.
     * Round all numeric values to 2 decimal places before including them.

4. IMPORTANT: Do NOT print the result. Do NOT import json. Just define the `result` dictionary in the global scope. The system will handle the JSON serialization automatically.

5. Do not use plt.show(), display(), or any GUI/interactive output.
"""

    llms = []
    
    # Setup Groq (Best for free single-shot without daily limits)
    try:
        from langchain_groq import ChatGroq
        llms.append(ChatGroq(
            api_key=app_settings.GROQ_API_KEY,
            model_name=app_settings.GENERATION_MODEL_ID,
            temperature=0
        ))
    except ImportError:
        pass

    # Setup Gemini
    if app_settings.GEMINI_API_KEY:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llms.append(ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=app_settings.GEMINI_API_KEY,
                temperature=0
            ))
        except ImportError:
            pass

    # Setup OpenAI
    if app_settings.OPENAI_API_KEY:
        try:
            from langchain_openai import ChatOpenAI
            llms.append(ChatOpenAI(
                api_key=app_settings.OPENAI_API_KEY,
                model="gpt-4o-mini",
                temperature=0
            ))
        except ImportError:
            pass

    if not llms:
        state["result"] = "No LLM provider available for data analysis."
        state["status"] = "error"
        return state

    response = None
    last_error = None
    
    for llm in llms:
        try:
            response = await llm.ainvoke(system_prompt)
            break # Success!
        except Exception as e:
            logger.warning(f"LLM {llm.__class__.__name__} failed: {e}")
            last_error = e

    if not response:
        logger.error(f"All LLMs failed. Last error: {last_error}")
        state["result"] = f"An error occurred during data analysis: All providers failed (Rate Limit or Quota Exhausted). Last error: {last_error}"
        state["status"] = "error"
        return state

    try:
        python_code = response.content.replace("```python", "").replace("```", "").strip()


        # Inject standard imports and safe JSON serialization boilerplate
        execution_code = f"""import json
import numpy as np
import pandas as pd

# LLM Generated Code
{python_code}

# Safe Serialization
class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, 'item'): return obj.item()
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        return super(NpEncoder, self).default(obj)

if 'result' in locals():
    print(json.dumps(result, cls=NpEncoder, ensure_ascii=False))
else:
    print(json.dumps({{"text_summary": "Error: result dictionary not defined by LLM code.", "chart_json": None}}))
"""

        # Execute the python code
        from langchain_experimental.utilities import PythonREPL
        repl = PythonREPL()
        result_str = repl.run(execution_code)
        
        try:
            clean_output = result_str.strip()
            output_json = json.loads(clean_output)
            text_summary = output_json.get("text_summary", "")
            chart_json = output_json.get("chart_json", None)

            state["result"] = text_summary or clean_output
            state["chart_data"] = chart_json
            state["status"] = "success"
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON from Python execution. Generated code was:\n{python_code}\n\nExecution Output:\n{result_str}")
            if "Traceback" in result_str or "Error" in result_str:
                state["result"] = f"حدث خطأ أثناء تشغيل الكود البرمجي لتحليل البيانات:\n{result_str}"
                state["status"] = "error"
            else:
                state["result"] = result_str
                state["status"] = "success"
            state["chart_data"] = None

    except Exception as e:
        logger.error(f"Error in data_analysis_node execution: {e}")
        state["result"] = f"An error occurred during python execution: {e}"
        state["status"] = "error"
        
    return state
