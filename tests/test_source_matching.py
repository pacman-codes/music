"""MusicBrainz-to-YouTube results are ranked suggestions, never auto files."""
from music_bot.source_matching import match_score, suggest_sources
from music_bot.youtube import VideoCandidate

ID1="dQw4w9WgXcQ"
ID2="kJQP7kiw5Fk"
ID3="M7lc1UVf-VE"
RECORD={"mbid":"8a49dba0-253a-4535-b87f-78bb035336ce",
        "title":"Roads","artist":"Portishead","duration_ms":300000}


def test_matching_prefers_original_right_artist_and_length():
    original=VideoCandidate(ID1,"Portishead - Roads (Official Audio)",
                            "Portishead",300)
    live=VideoCandidate(ID2,"Portishead - Roads (Live)", "Concerts",350)
    wrong=VideoCandidate(ID3,"Some unrelated track","Unknown",300)
    ranked=suggest_sources(RECORD,[live,wrong,original])
    assert [item.video.video_id for item in ranked]==[ID1,ID2]
    assert ranked[0].score>ranked[1].score
    assert ranked[0].score>=0.9
    assert "Возможно ремикс, кавер или live" in ranked[1].warnings
    assert all(item.public()["playable"] is False for item in ranked)
    assert all(item.public()["match_confirmed"] is False for item in ranked)


def test_uncertain_length_and_credits_are_explicitly_marked():
    video=VideoCandidate(ID1,"Roads","Random uploader",None)
    match=match_score(RECORD,video)
    assert match is not None
    assert "Не удалось сравнить длительность" in match.warnings
    assert "Не совпал исполнитель" in match.warnings
    assert match.score < 0.8


def test_unrelated_video_rejected_even_with_same_artist():
    video=VideoCandidate(ID1,"Teardrop - Official Video","Portishead",300)
    assert match_score(RECORD,video) is None
