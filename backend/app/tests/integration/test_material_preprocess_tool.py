from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError
import pytest

from app.schemas.evidence import ArtifactKind
from app.schemas.task import TaskType
from app.services.artifact_service import ArtifactService, StorageGroup
from app.tools.material_preprocess_tool import MaterialPreprocessTool
from app.workflows.graph_io import load_material_pack_from_manifest_ref, merge_material_packs


@pytest.fixture
def material_service(tmp_path: Path):
    service = ArtifactService(tmp_path / 'artifacts', tmp_path / 'sqlite' / 'reports.db')
    task_id = service.create_task('Project progress', TaskType.WEEKLY_REPORT)
    return service, task_id


def save_image(service, task_id, image_format='PNG'):
    content = BytesIO()
    Image.new('RGB', (48, 24), color='green').save(content, format=image_format)
    suffix = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp', 'BMP': 'bmp'}[image_format]
    return service.save_bytes(task_id, ArtifactKind.IMAGE_MATERIAL, f'progress.{suffix}', content.getvalue(), StorageGroup.UPLOAD)


@pytest.mark.parametrize('image_format', ['PNG', 'JPEG', 'WEBP'])
def test_preprocess_reads_original_image_and_persists_manifest(material_service, image_format):
    service, task_id = material_service
    source = save_image(service, task_id, image_format)
    pack = MaterialPreprocessTool(service).preprocess_image(
        task_id, source, TaskType.WEEKLY_REPORT, note='  Milestone complete  ',
    )
    assert pack.metadata['width'] == '48'
    assert pack.metadata['height'] == '24'
    assert pack.metadata['source_type'] == 'image'
    material = pack.materials[0]
    assert material.image_path == source.path
    assert material.source_artifact_id == source.artifact_id
    assert material.note == 'Milestone complete'
    assert load_material_pack_from_manifest_ref(pack.manifest).materials == pack.materials


@pytest.mark.parametrize('image_format', ['BMP', 'invalid', 'video'])
def test_preprocess_rejects_unsupported_or_invalid_material(material_service, image_format):
    service, task_id = material_service
    if image_format == 'BMP':
        source = save_image(service, task_id, image_format)
    else:
        source = service.save_bytes(
            task_id,
            ArtifactKind.MANIFEST if image_format == 'video' else ArtifactKind.IMAGE_MATERIAL,
            'invalid.png', b'not an image', StorageGroup.UPLOAD,
        )
    with pytest.raises((ValueError, UnidentifiedImageError)):
        MaterialPreprocessTool(service).preprocess_image(task_id, source, TaskType.WEEKLY_REPORT)


def test_merge_keeps_multiple_original_images(material_service):
    service, task_id = material_service
    tool = MaterialPreprocessTool(service)
    first = tool.preprocess_image(task_id, save_image(service, task_id), TaskType.WEEKLY_REPORT)
    second = tool.preprocess_image(task_id, save_image(service, task_id, 'JPEG'), TaskType.WEEKLY_REPORT)
    merged = merge_material_packs(first, second)
    assert len({material.material_id for material in merged.materials}) == 2
    assert [material.image_path for material in merged.materials] == [first.materials[0].image_path, second.materials[0].image_path]
    assert all(Path(material.image_path).is_file() for material in merged.materials)
