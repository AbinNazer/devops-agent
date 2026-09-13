"""Local worker registration payload builder; transport remains outbound."""
import platform, socket
from worker.capability_registry import advertised_capabilities
def registration_payload(name="local-worker", version="development"):
    return {"name": name, "version": version, "platform": platform.platform(), "hostname": socket.gethostname(), "capabilities": advertised_capabilities()}