"""Control-plane coordination with scoped, single-use worker enrollment."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
import hashlib, hmac, os, secrets, threading, uuid


def _now(): return datetime.now(timezone.utc).replace(tzinfo=None)
def _id(kind): return f"{kind}_{uuid.uuid4().hex}"
def _hash(token): return hashlib.sha256(f"{os.getenv('WORKER_TOKEN_HASH_SECRET', 'local-development-only')}:{token}".encode()).hexdigest()

@dataclass
class Organization:
    id: str; name: str; status: str = "active"; created_at: datetime = field(default_factory=_now)
@dataclass
class Project:
    id: str; organization_id: str; name: str; description: str = ""; created_at: datetime = field(default_factory=_now)
@dataclass
class Infrastructure:
    id: str; organization_id: str; project_id: str; name: str; type: str; status: str = "offline"; worker_id: str | None = None; capabilities: list[str] = field(default_factory=list); last_seen: datetime | None = None
@dataclass
class Worker:
    id: str; organization_id: str; project_id: str; infrastructure_id: str; name: str; version: str; platform: str; hostname: str; capabilities: list[str]; token_hash: str; status: str = "online"; last_seen: datetime = field(default_factory=_now)
@dataclass
class Enrollment:
    organization_id: str; project_id: str; infrastructure_id: str; expires_at: datetime; used: bool = False

class ControlPlaneService:
    """Local development store; its API is the future MySQL repository boundary."""
    def __init__(self):
        self._lock = threading.RLock(); self.organizations = {}; self.projects = {}; self.infrastructure = {}; self.workers = {}; self._enrollments = {}
    @staticmethod
    def public(item):
        data = asdict(item); data.pop("token_hash", None); return data
    def create_organization(self, name):
        if not name.strip(): raise ValueError("Organization name is required")
        item = Organization(_id("org"), name.strip())
        with self._lock: self.organizations[item.id] = item
        return item
    def create_project(self, organization_id, name, description=""):
        with self._lock:
            if organization_id not in self.organizations: raise KeyError("Organization not found")
            item = Project(_id("project"), organization_id, name.strip(), description); self.projects[item.id] = item; return item
    def create_infrastructure(self, organization_id, project_id, name, kind):
        with self._lock:
            project = self.projects.get(project_id)
            if not project or project.organization_id != organization_id: raise PermissionError("Project is not available in this organization")
            item = Infrastructure(_id("infra"), organization_id, project_id, name.strip(), kind); self.infrastructure[item.id] = item; return item
    def list_infrastructure(self, organization_id):
        return [i for i in self.infrastructure.values() if i.organization_id == organization_id]
    def create_enrollment(self, organization_id, project_id, infrastructure_id, ttl_minutes=15):
        with self._lock:
            i = self.infrastructure.get(infrastructure_id)
            if not i or (i.organization_id, i.project_id) != (organization_id, project_id): raise PermissionError("Infrastructure scope mismatch")
            token = secrets.token_urlsafe(32); self._enrollments[_hash(token)] = Enrollment(organization_id, project_id, infrastructure_id, _now()+timedelta(minutes=max(1,min(ttl_minutes,60)))); return token
    def register_worker(self, token, *, name, version, platform, hostname, capabilities):
        with self._lock:
            e = self._enrollments.get(_hash(token))
            if not e or e.used or e.expires_at <= _now(): raise PermissionError("Enrollment token is invalid or expired")
            e.used = True; worker_token = secrets.token_urlsafe(32)
            w = Worker(_id("worker"), e.organization_id, e.project_id, e.infrastructure_id, name, version, platform, hostname, sorted(set(capabilities)), _hash(worker_token)); self.workers[w.id] = w
            i = self.infrastructure[w.infrastructure_id]; i.worker_id=w.id; i.status="online"; i.capabilities=w.capabilities; i.last_seen=w.last_seen
            return w, worker_token
    def heartbeat(self, worker_id, worker_token, *, version, platform, hostname, capabilities, status="online"):
        with self._lock:
            w = self.workers.get(worker_id)
            if not w or not hmac.compare_digest(w.token_hash, _hash(worker_token)): raise PermissionError("Worker authentication failed")
            w.version,w.platform,w.hostname,w.capabilities,w.status,w.last_seen = version,platform,hostname,sorted(set(capabilities)),status,_now()
            i=self.infrastructure[w.infrastructure_id]; i.status=status; i.capabilities=w.capabilities; i.last_seen=w.last_seen
            return w

_control_plane = None
def get_control_plane():
    global _control_plane
    if _control_plane is None: _control_plane = ControlPlaneService()
    return _control_plane