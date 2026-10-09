from yt_player.env import env_float, env_int, env_str


MEDIA_SERVICE_URL = env_str("MEDIA_SERVICE_URL", "http://127.0.0.1:5454")
MEDIA_SERVICE_TIMEOUT_SECONDS = env_float("MEDIA_SERVICE_TIMEOUT_SECONDS", 30.0, minimum=1.0)

MCP_TRANSPORT = env_str("MCP_TRANSPORT", "stdio").lower()
MCP_HOST = env_str("MCP_HOST", "127.0.0.1")
MCP_PORT = env_int("MCP_PORT", 5455, minimum=1, maximum=65535)
MCP_STREAMABLE_HTTP_PATH = env_str("MCP_STREAMABLE_HTTP_PATH", "/mcp")
MCP_SSE_PATH = env_str("MCP_SSE_PATH", "/sse")
MCP_MESSAGE_PATH = env_str("MCP_MESSAGE_PATH", "/messages/")
