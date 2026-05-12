from __future__ import annotations


def test_home_ltr(client):
    response = client.get("/?ui_locale=en-US")
    assert response.status_code == 200
    text = response.text
    assert 'lang="en-US"' in text
    assert 'dir="ltr"' in text
    assert "Search" in text


def test_home_rtl(client):
    response = client.get("/?ui_locale=ar")
    assert response.status_code == 200
    text = response.text
    assert 'lang="ar"' in text
    assert 'dir="rtl"' in text
    assert "بحث" in text


def test_results_use_dir_auto(client):
    response = client.get("/search?q=python+مكتبة+البحث&type=web&ui_locale=ar")
    assert response.status_code == 200
    text = response.text
    assert 'dir="rtl"' in text  # html element
    # Each result card uses dir="auto"
    assert 'class="result-card"' in text
    assert 'dir="auto"' in text


def test_image_grid_renders(client):
    response = client.get("/search?q=cats&type=images&ui_locale=ru-RU&limit=8")
    assert response.status_code == 200
    text = response.text
    assert 'class="image-grid"' in text
    assert 'data-lightbox' in text


def test_search_page_non_numeric_does_not_crash(client):
    response = client.get("/search?q=hello&page=abc")
    assert response.status_code == 200


def test_search_invalid_safe_search_falls_back(client):
    response = client.get("/search?q=hello&safe_search=invalid")
    assert response.status_code == 200


def test_search_invalid_time_range_falls_back(client):
    response = client.get("/search?q=hello&time_range=eternity")
    assert response.status_code == 200


def test_search_invalid_image_filters_fall_back(client):
    response = client.get(
        "/search?q=cats&type=images&size=galactic&orientation=diagonal"
    )
    assert response.status_code == 200
    assert 'class="image-grid"' in response.text
