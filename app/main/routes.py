
from flask import request, jsonify, render_template
from . import main
from ..services import agent_service

@main.route("/")
def index():
    """
    Serves the main index.html page.
    """
    return render_template("index.html")

@main.route("/search", methods=["POST"])
async def search():
    """
    The main endpoint to trigger the agentic pipeline.
    """
    data = request.get_json()
    query = data.get("query")
    if not query:
        return jsonify({"error": "Query is required"}), 400

    result = await agent_service.run_agentic_pipeline(query)
    
    return jsonify(result)
