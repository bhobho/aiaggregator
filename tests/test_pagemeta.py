from aiaggregator.ingest.pagemeta import _extract_og_image

BASE = "https://example.com/posts/1"


def test_extract_og_image_basic():
    html = """
    <html><head>
      <meta property="og:title" content="A story">
      <meta property="og:image" content="https://cdn.example.com/img.jpg">
    </head><body></body></html>
    """
    assert _extract_og_image(html, BASE) == "https://cdn.example.com/img.jpg"


def test_extract_og_image_content_before_property():
    html = '<meta content="https://cdn.example.com/img2.jpg" property="og:image">'
    assert _extract_og_image(html, BASE) == "https://cdn.example.com/img2.jpg"


def test_extract_twitter_image_fallback():
    html = '<meta name="twitter:image" content="/relative/img3.jpg">'
    assert _extract_og_image(html, BASE) == "https://example.com/relative/img3.jpg"


def test_extract_og_image_none_present():
    html = "<html><head><title>No image here</title></head></html>"
    assert _extract_og_image(html, BASE) is None


def test_extract_og_image_ignored_after_head_close():
    html = (
        "<head><title>x</title></head>"
        '<body><meta property="og:image" content="https://cdn.example.com/late.jpg"></body>'
    )
    assert _extract_og_image(html, BASE) is None
