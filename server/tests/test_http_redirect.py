from home_os.http_redirect import redirect_host, redirect_location


def test_redirect_location_removes_http_port():
    assert (
        redirect_location("192.168.0.8:80", "/media?tab=apps")
        == "https://192.168.0.8/media?tab=apps"
    )


def test_redirect_location_supports_local_hostname_and_ipv6():
    assert redirect_location("HomeOS.local", "/") == "https://homeos.local/"
    assert redirect_location("[fddc:cd9f:9a62::1]:80", "/health") == (
        "https://[fddc:cd9f:9a62::1]/health"
    )


def test_redirect_host_rejects_injected_or_invalid_values():
    assert redirect_host("good.example@evil.example") == "HomeOS.local"
    assert redirect_host("evil.example") == "HomeOS.local"
    assert redirect_host("8.8.8.8") == "HomeOS.local"
    assert redirect_host("bad host") == "HomeOS.local"
    assert redirect_host("") == "HomeOS.local"
