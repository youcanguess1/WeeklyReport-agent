import pytest

from app.schemas.evidence import ImageMaterial, MaterialPack, MaterialUnderstanding
from app.workflows.graph_io import merge_material_packs


def pack(task_id="task-1"):
    return MaterialPack(
        material_pack_id="materials-1", task_id=task_id, source_artifact_id="artifact-1",
        materials=[ImageMaterial(material_id="image-1", source_artifact_id="artifact-1", image_path="image.png", note="用户补充")],
        understandings=[MaterialUnderstanding(material_id="image-1", content_summary="图片内容")],
    )


def test_merge_remaps_colliding_material_ids_and_understandings():
    base, supplement = pack(), pack()
    merged = merge_material_packs(base, supplement)
    assert [item.material_id for item in merged.materials] == ["image-1", "image-1_1"]
    assert [item.material_id for item in merged.understandings] == ["image-1", "image-1_1"]
    assert merged.materials[1].note == "用户补充"
    assert len(base.materials) == len(supplement.materials) == 1
    assert merged.metadata["supplement_artifact_ids"] == "artifact-1"


def test_merge_rejects_cross_task_materials():
    with pytest.raises(ValueError, match="different tasks"):
        merge_material_packs(pack(), pack("task-2"))
