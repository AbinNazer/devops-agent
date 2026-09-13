import pytest
from app.control_plane.service import ControlPlaneService


def setup_control_plane():
    cp = ControlPlaneService()
    org = cp.create_organization("Acme")
    project = cp.create_project(org.id, "Platform")
    infra = cp.create_infrastructure(org.id, project.id, "Developer Machine", "local")
    return cp, org, project, infra


def test_worker_enrollment_is_single_use_and_secret_is_not_public():
    cp, org, project, infra = setup_control_plane()
    token = cp.create_enrollment(org.id, project.id, infra.id)
    worker, worker_token = cp.register_worker(token, name="local", version="1", platform="linux", hostname="dev", capabilities=["system.cpu"])
    assert "token_hash" not in cp.public(worker)
    assert worker_token
    with pytest.raises(PermissionError):
        cp.register_worker(token, name="again", version="1", platform="linux", hostname="dev", capabilities=[])


def test_worker_heartbeat_requires_its_own_token():
    cp, org, project, infra = setup_control_plane()
    enrollment = cp.create_enrollment(org.id, project.id, infra.id)
    worker, token = cp.register_worker(enrollment, name="local", version="1", platform="linux", hostname="dev", capabilities=[])
    with pytest.raises(PermissionError):
        cp.heartbeat(worker.id, "wrong", version="2", platform="linux", hostname="dev", capabilities=[])
    updated = cp.heartbeat(worker.id, token, version="2", platform="linux", hostname="dev", capabilities=["system.memory"])
    assert updated.version == "2"
    assert infra.status == "online"


def test_cross_organization_infrastructure_creation_is_denied():
    cp, _, project, _ = setup_control_plane()
    other = cp.create_organization("Other")
    with pytest.raises(PermissionError):
        cp.create_infrastructure(other.id, project.id, "not allowed", "local")