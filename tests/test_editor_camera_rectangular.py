from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_editor_has_independent_width_height_and_camera_zoom_pan():
    text=(ROOT/"editor.py").read_text(encoding="utf-8")
    assert "Width (metres):" in text and "Height (metres):" in text
    assert "def zoom_at(" in text
    assert "def pan(" in text
    assert "pygame.MOUSEWHEEL" in text
    assert "pygame.K_LEFT,pygame.K_RIGHT,pygame.K_UP,pygame.K_DOWN" in text
    assert "min(MAP.w/max(1.0,ww),MAP.h/max(1.0,wh))" in text

def test_main_camera_uses_independent_world_dimensions():
    text=(ROOT/"ui"/"view.py").read_text(encoding="utf-8")
    assert 'WORLD_W = float(sim.world.get("width_m", 4000))' in text
    assert 'WORLD_H = float(sim.world.get("height_m", 4000))' in text
    assert 'min(map_rect.w / WORLD_W, map_rect.h / WORLD_H)' in text
