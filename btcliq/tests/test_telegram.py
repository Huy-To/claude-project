from btcliq.telegram import normalize_channel, parse_page

PAGE = """
<html><head><meta property="og:title" content="Test News"></head><body>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
 <div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="TestNews/101">
  <div class="tgme_widget_message_bubble">
   <div class="tgme_widget_message_forwarded_from accent_color">Forwarded from
     <a class="tgme_widget_message_forwarded_from_name" href="https://t.me/other"><span>Other Chan</span></a></div>
   <div class="tgme_widget_message_text js-message_text" dir="auto">BREAKING: <b>BTC</b> &amp; ETH up<br/>Second line
     <a href="https://example.com/story">story</a><div class="inner">nested</div> tail</div>
   <a class="tgme_widget_message_photo_wrap" style="width:100px;background-image:url('https://cdn.example/p.jpg')"></a>
   <div class="tgme_widget_message_footer"><span class="tgme_widget_message_views">12.3K</span>
     <a class="tgme_widget_message_date" href="https://t.me/TestNews/101"><time datetime="2026-11-09T06:00:00+00:00" class="time">06:00</time></a></div>
  </div>
 </div>
</div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="TestNews/102">
  <div class="tgme_widget_message_text js-message_text">Short one</div>
  <time datetime="2026-11-09T07:30:00+00:00"></time></div></div>
</body></html>
"""


def test_parse_page_extracts_posts():
    title, posts = parse_page(PAGE, "TestNews")
    assert title == "Test News"
    assert [p["id"] for p in posts] == ["testnews/101", "testnews/102"]
    p = posts[0]
    assert p["text"].startswith("BREAKING: BTC & ETH up\nSecond line")
    assert "nested" in p["text"] and p["text"].endswith("tail")
    assert p["url"] == "https://t.me/TestNews/101"
    assert p["views"] == "12.3K"
    assert p["photo"] == "https://cdn.example/p.jpg"
    assert p["forwarded"] == "Other Chan"
    assert p["links"] == ["https://example.com/story"]
    assert p["time"] == 1794204000000
    assert posts[1]["text"] == "Short one" and posts[1]["forwarded"] is None


def test_normalize_channel():
    assert normalize_channel("@WatcherGuru") == "WatcherGuru"
    assert normalize_channel("https://t.me/s/WalterBloomberg") == "WalterBloomberg"
    assert normalize_channel("t.me/durov/552") == "durov"
    assert normalize_channel("telegram.me/Some_Chan?x=1") == "Some_Chan"
    assert normalize_channel("bad name!") is None
    assert normalize_channel("abc") is None  # too short for a public username
