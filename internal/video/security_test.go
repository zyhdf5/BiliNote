package video

import "testing"

func TestMatchesDomain(t *testing.T) {
	allowed := []string{"youtube.com", "bilibili.com"}
	for _, h := range []string{"youtube.com", "www.youtube.com", "api.bilibili.com"} {
		if !matchesDomain(h, allowed) {
			t.Fatalf("expected allowed: %s", h)
		}
	}
	for _, h := range []string{"youtube.com.evil.test", "notbilibili.com"} {
		if matchesDomain(h, allowed) {
			t.Fatalf("expected denied: %s", h)
		}
	}
}
