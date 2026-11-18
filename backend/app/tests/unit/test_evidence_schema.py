import pytest
from pydantic import ValidationError

from app.schemas.evidence import ImageMaterial, MaterialPack, MaterialUnderstanding


def test_image_material_pack_round_trip():
    pack = MaterialPack(
        material_pack_id="materials-1", task_id="task-1", source_artifact_id="image-1",
        materials=[ImageMaterial(material_id="image-1", source_artifact_id="image-1", image_path="/tmp/image.png", note="完成率 80%")],
        understandings=[MaterialUnderstanding(material_id="image-1", content_summary="进度图显示 80%")],
    )
    assert MaterialPack.model_validate_json(pack.model_dump_json()) == pack
    assert set(pack.materials[0].model_dump()) == {"material_id", "source_artifact_id", "image_path", "note"}


@pytest.mark.parametrize("case", ["duplicate_material", "duplicate_understanding", "unknown_material"])
def test_material_pack_rejects_ambiguous_references(case):
    material = ImageMaterial(material_id="image-1", source_artifact_id="artifact-1", image_path="image.png")
    summary = MaterialUnderstanding(material_id="image-1", content_summary="进度")
    with pytest.raises(ValidationError):
        MaterialPack(
            material_pack_id="materials-1", task_id="task-1", source_artifact_id="artifact-1",
            materials=[material, material] if case == "duplicate_material" else [material],
            understandings=[summary, summary] if case == "duplicate_understanding" else [MaterialUnderstanding(material_id="unknown", content_summary="进度")] if case == "unknown_material" else [],
        )
