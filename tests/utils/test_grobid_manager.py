from utils.grobid_manager import GrobidServerManager


def test_deprecated_grobid_080_tag_is_remapped():
    manager = GrobidServerManager(
        {"quality_control": {"grobid": {"docker_image": "lfoppiano/grobid:0.8.0"}}}
    )
    assert manager.image == "lfoppiano/grobid:0.9.0-crf"


def test_deprecated_grobid_080_crf_tag_is_remapped():
    manager = GrobidServerManager(
        {"quality_control": {"grobid": {"docker_image": "lfoppiano/grobid:0.8.0-crf"}}}
    )
    assert manager.image == "lfoppiano/grobid:0.9.0-crf"


def test_non_deprecated_grobid_tag_is_preserved():
    manager = GrobidServerManager(
        {"quality_control": {"grobid": {"docker_image": "lfoppiano/grobid:latest-crf"}}}
    )
    assert manager.image == "lfoppiano/grobid:latest-crf"
