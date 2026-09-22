from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_editor_defaults_to_blank_not_demo():
    text=(ROOT/"editor.py").read_text(encoding="utf-8")
    assert "def __init__(self,scenario=None)" in text
    assert "self.new_blank()" in text
    assert "default='scenarios/demo.json'" not in text

def test_main_has_no_implicit_demo_start():
    text=(ROOT/"main.py").read_text(encoding="utf-8")
    start=text[text.index("def main():"):]
    assert 'default=None' in start
    assert 'No scenario selected; simulator not started.' in start
