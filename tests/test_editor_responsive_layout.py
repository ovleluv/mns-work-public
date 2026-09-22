from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_editor_uses_resizable_window_and_dynamic_panel_geometry():
    text=(ROOT/'editor.py').read_text(encoding='utf-8')
    assert 'pygame.RESIZABLE' in text
    assert 'pygame.VIDEORESIZE' in text
    assert 'def update_layout' in text
    assert 'PANEL_MIN_W' in text and 'PANEL_MAX_W' in text
    assert 'PANEL.bottom' in text
    assert 'wrap_text' in text and 'fit_text' in text
    # Legacy fixed panel/status coordinates must not return.
    assert 'x=1180' not in text
    assert 'pygame.Rect(x,828' not in text
    assert '(x,870)' not in text
