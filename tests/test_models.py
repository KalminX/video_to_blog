import pytest
from models import Video

def test_video_model_defaults(db_session):
    """Test Video model default fields."""
    video = Video(filename='test.mp4', filepath='/tmp/test.mp4', user_id='123')
    db_session.add(video)
    db_session.commit()

    assert video.id is not None
    assert video.filename == 'test.mp4'
    assert video.filepath == '/tmp/test.mp4'
    assert video.transcript is None
    assert video.article is None
    assert isinstance(video.created_at, object)
