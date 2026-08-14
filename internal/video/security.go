package video

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"strings"
)

type URLGuard struct {
	AllowedDomains      []string
	AllowPrivateURLs    bool
	AllowUnlistedDomain bool
	Resolver            *net.Resolver
}

func (g URLGuard) Validate(ctx context.Context, raw string) error {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" {
		return fmt.Errorf("invalid http(s) URL")
	}
	host := strings.ToLower(strings.TrimSuffix(u.Hostname(), "."))
	if !g.AllowUnlistedDomain && !matchesDomain(host, g.AllowedDomains) {
		return fmt.Errorf("domain %q is not allowed", host)
	}
	if g.AllowPrivateURLs {
		return nil
	}
	resolver := g.Resolver
	if resolver == nil {
		resolver = net.DefaultResolver
	}
	ips, err := resolver.LookupIP(ctx, "ip", host)
	if err != nil {
		return fmt.Errorf("resolve host: %w", err)
	}
	for _, ip := range ips {
		if ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast() {
			return fmt.Errorf("private/reserved address is forbidden: %s", ip)
		}
	}
	return nil
}

func matchesDomain(host string, allowed []string) bool {
	for _, d := range allowed {
		d = strings.ToLower(strings.TrimPrefix(strings.TrimSpace(d), "."))
		if host == d || strings.HasSuffix(host, "."+d) {
			return true
		}
	}
	return false
}
