from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_editor_polygon_autoclose_contract():
    text=(ROOT/'editor.py').read_text(encoding='utf-8')
    assert "POLYGON_TOOLS={'WOODS','FOREST','BRUSH','URBAN'}" in text
    assert 'POLYGON_CLOSE_RADIUS_PX=14' in text
    assert 'def should_close_polygon(self, screen_pos):' in text
    assert 'len(self.points)<3' in text
    assert 'math.dist(screen_pos,first)<=POLYGON_CLOSE_RADIUS_PX' in text
    assert 'elif self.should_close_polygon(e.pos):' in text
    assert "self.finish_shape(); self.message=f'{self.tool} polygon closed'" in text
    assert 'Area: click near first point to close' in text
