import asyncio
import base64
import os
import tempfile
import subprocess
import uuid
import logging
from pathlib import Path
from typing import Dict, Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent, ImageContent

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("diagram-mcp")

server = Server("diagram_mcp")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="generate_diagram",
            description="Generate a diagram using Matplotlib or Manim. For matplotlib, save to 'output.png'. For manim, define a Scene class and it will be rendered.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Description of the diagram"
                    },
                    "code": {
                        "type": "string",
                        "description": "Python script to execute"
                    },
                    "engine": {
                        "type": "string",
                        "enum": ["matplotlib", "manim"],
                        "description": "Engine to use. Matplotlib is faster for static 2D plots. Manim is best for math animations and premium visuals."
                    }
                },
                "required": ["prompt", "code", "engine"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent]:
    if name != "generate_diagram":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    prompt = arguments.get("prompt", "")
    code = arguments.get("code", "")
    engine = arguments.get("engine", "matplotlib")

    if not code:
        return [TextContent(type="text", text="Error: code is required")]

    try:
        if engine == "matplotlib":
            return await run_matplotlib(prompt, code)
        elif engine == "manim":
            return await run_manim(prompt, code)
        else:
            return [TextContent(type="text", text=f"Error: unknown engine {engine}")]
    except Exception as e:
        logger.error(f"Error executing diagram: {e}")
        return [TextContent(type="text", text=f"Error: {str(e)}")]

async def run_matplotlib(prompt: str, code: str) -> list[TextContent | ImageContent]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        script_path = temp_path / "script.py"
        output_path = temp_path / "output.png"
        
        # Inject output path expectation
        if "output.png" not in code and "savefig" not in code:
            code += "\nimport matplotlib.pyplot as plt\nplt.savefig('output.png', dpi=300, bbox_inches='tight')\n"
            
        script_path.write_text(code)
        
        # Execute script
        process = await asyncio.create_subprocess_exec(
            "python", "script.py",
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            process.kill()
            return [TextContent(type="text", text="Error: Matplotlib execution timed out (30s)")]
            
        if process.returncode != 0:
            return [TextContent(type="text", text=f"Error executing script:\n{stderr.decode()}")]
            
        if not output_path.exists():
            return [TextContent(type="text", text="Error: Script completed but output.png was not generated.")]
            
        img_data = output_path.read_bytes()
        img_base64 = base64.b64encode(img_data).decode('utf-8')
        
        return [
            TextContent(type="text", text=f"Generated diagram for: {prompt}\nEngine: matplotlib"),
            ImageContent(type="image", data=img_base64, mimeType="image/png")
        ]

async def run_manim(prompt: str, code: str) -> list[TextContent | ImageContent]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        script_path = temp_path / "script.py"
        script_path.write_text(code)
        
        # Extract Scene name using a simple heuristic (last defined Scene class)
        scene_name = ""
        for line in code.split('\n'):
            if line.startswith("class ") and "(Scene)" in line:
                scene_name = line.split("class ")[1].split("(")[0].strip()
                
        if not scene_name:
            return [TextContent(type="text", text="Error: Could not find a class inheriting from Scene in the script.")]
        
        # Execute manim: -s (save last frame), -q h (high quality)
        process = await asyncio.create_subprocess_exec(
            "manim", "-s", "-q", "h", "--format=png", "script.py", scene_name,
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120.0)
        except asyncio.TimeoutError:
            process.kill()
            return [TextContent(type="text", text="Error: Manim execution timed out (120s)")]
            
        if process.returncode != 0:
            return [TextContent(type="text", text=f"Error executing manim:\n{stderr.decode()}")]
            
        # Find the output image
        # Manim outputs to media/images/script/<SceneName>.png usually
        images_dir = temp_path / "media" / "images" / "script"
        if not images_dir.exists():
            return [TextContent(type="text", text=f"Error: Manim media directory not found. Stderr: {stderr.decode()}")]
            
        output_files = list(images_dir.glob("*.png"))
        if not output_files:
            return [TextContent(type="text", text=f"Error: Manim did not generate any PNG files. Stderr: {stderr.decode()}")]
            
        output_path = output_files[0]
        img_data = output_path.read_bytes()
        img_base64 = base64.b64encode(img_data).decode('utf-8')
        
        return [
            TextContent(type="text", text=f"Generated diagram for: {prompt}\nEngine: manim"),
            ImageContent(type="image", data=img_base64, mimeType="image/png")
        ]

async def stdio_main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

def create_app():
    app = Starlette(debug=True)
    transport = SseServerTransport("/messages")

    async def handle_sse(request):
        async with transport.connect_sse(
            request.scope, request.receive, request._send
        ) as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    async def handle_messages(request):
        await transport.handle_post_message(request.scope, request.receive, request._send)

    app.add_route("/sse", handle_sse, methods=["GET"])
    app.add_route("/messages", handle_messages, methods=["POST"])
    return app

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sse", action="store_true", help="Run SSE server instead of stdio")
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE server")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host for SSE server")
    args = parser.parse_args()

    if args.sse:
        logger.info(f"Starting SSE server on {args.host}:{args.port}")
        uvicorn.run(create_app(), host=args.host, port=args.port)
    else:
        logger.info("Starting stdio server")
        asyncio.run(stdio_main())
