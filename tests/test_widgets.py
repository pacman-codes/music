import pytest
from music_bot.widgets import parse_rating_request, rating_widget_html

def test_widget_escapes_and_is_small():
    widget = rating_widget_html("<script>evil</script>", "A & B", 8)
    assert "&lt;script&gt;evil&lt;/script&gt;" in widget
    assert "<script>evil</script>" not in widget
    assert "A &amp; B" in widget
    assert widget.count('data-v="rate:') == 10
    assert 'data-v="skip"' in widget
    assert "TelegramWidget.request(v)" in widget
    assert "fetch(" not in widget

@pytest.mark.parametrize("data,result", [
    ("rate:1",("rate",1)), ("rate:10",("rate",10)),
    ("skip",("skip",None)), ("complete",("complete",None)),
])
def test_parse_widget_actions(data,result):
    action=parse_rating_request(data)
    assert (action.action,action.score)==result

@pytest.mark.parametrize("bad", [
    "rate:0","rate:11","rate:01","rate:2;kill","skip:1","", "q"*65
])
def test_invalid_action_rejected(bad):
    with pytest.raises(ValueError): parse_rating_request(bad)

def test_invalid_rating_rejected():
    with pytest.raises(ValueError): rating_widget_html("One","Two",True)
    with pytest.raises(ValueError): rating_widget_html("One","Two",11)
