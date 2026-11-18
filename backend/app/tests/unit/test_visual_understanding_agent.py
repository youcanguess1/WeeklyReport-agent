from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from PIL import Image
from pydantic import Field
import pytest

from app.agents.visual_understanding_agent import VisualUnderstandingAgent
from app.schemas.evidence import MaterialUnderstanding, ImageMaterial, MaterialPack
from app.workflows.graph_nodes import WeeklyReportGraphNodes
from app.workflows.graph_state import TaskState


class ImageModel(BaseChatModel):
    seen: list[Any] = Field(default_factory=list)
    summary: str = 'Visible project progress.'

    @property
    def _llm_type(self):
        return 'image-test-model'

    def bind_tools(self, tools, **kwargs):
        raise AssertionError('Image understanding must not request frame tools.')

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.summary))])

    def with_structured_output(self, schema, **kwargs):
        return RunnableLambda(lambda messages: schema(content_summary=self.summary))


def image_pack(tmp_path: Path):
    materials = []
    for index, color in enumerate(['red', 'blue']):
        path = tmp_path / f'image-{index}.png'
        Image.new('RGB', (12, 12), color=color).save(path)
        materials.append(ImageMaterial(material_id=f'material-{index}', source_artifact_id=f'artifact-{index}', image_path=str(path), note=f'Note for material {index}'))
    return MaterialPack(material_pack_id='evidence-images', task_id='task-images', source_artifact_id='artifact-0', materials=materials)


def test_each_image_has_isolated_context_and_direct_image_content(tmp_path):
    pack = image_pack(tmp_path)
    model = ImageModel()
    agent = VisualUnderstandingAgent(model)
    understood = agent.understand_material_pack(pack)
    assert [item.material_id for item in understood.understandings] == ['material-0', 'material-1']
    assert len(model.seen) == 2
    image_urls = []
    for index, messages in enumerate(model.seen):
        assert len(messages) == 2
        human = next(message for message in messages if isinstance(message, HumanMessage))
        assert f'Note for material {index}' in human.content[0]['text']
        assert f'Note for material {1 - index}' not in human.content[0]['text']
        image_urls.append(human.content[1]['image_url']['url'])
    assert all(url.startswith('data:image/png;base64,') for url in image_urls)
    assert image_urls[0] != image_urls[1]
    assert len(agent.last_messages) == 6


def test_supplements_only_analyze_new_images_and_clear_cached_messages(tmp_path):
    pack = image_pack(tmp_path)
    existing = MaterialUnderstanding(material_id='material-0', content_summary='Confirmed earlier summary.')
    pack.understandings = [existing]
    model = ImageModel()
    agent = VisualUnderstandingAgent(model)
    understood = agent.understand_material_pack(pack)
    assert len(model.seen) == 1
    assert understood.understandings[0] == existing
    agent.understand_material_pack(understood)
    assert len(model.seen) == 1
    assert agent.last_messages == []
    assert agent.last_turn_messages == []
    assert agent.last_run_debug == {'materials': []}


@pytest.mark.parametrize('invalid', ['missing_image', 'empty_summary'])
def test_invalid_visual_evidence_fails_explicitly(tmp_path, invalid):
    pack = image_pack(tmp_path)
    if invalid == 'missing_image':
        Path(pack.materials[0].image_path).unlink()
    model = ImageModel(summary=' ' if invalid == 'empty_summary' else 'Summary')
    with pytest.raises((ValueError, FileNotFoundError)):
        VisualUnderstandingAgent(model).understand_material_pack(pack)


def test_material_workflow_reports_missing_vision_model():
    state = TaskState(task_id='task-images', thread_id='thread-images', run_id='run-images')
    with pytest.raises(RuntimeError, match='vision model is required'):
        WeeklyReportGraphNodes().material_understanding(state)
