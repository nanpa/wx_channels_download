package application

import "testing"

func TestApplicationPageURL(t *testing.T) {
	tests := []struct {
		name     string
		protocol string
		hostname string
		port     int
		want     string
	}{
		{
			name:     "defaults to HTTP and loopback",
			protocol: "",
			hostname: "",
			port:     2023,
			want:     "http://127.0.0.1:2023",
		},
		{
			name:     "replaces an all-interface IPv4 listener",
			protocol: "http",
			hostname: "0.0.0.0",
			port:     8080,
			want:     "http://127.0.0.1:8080",
		},
		{
			name:     "formats an IPv6 loopback URL",
			protocol: "https",
			hostname: "::",
			port:     8443,
			want:     "https://[::1]:8443",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := application_page_url(tt.protocol, tt.hostname, tt.port); got != tt.want {
				t.Fatalf("application_page_url() = %q, want %q", got, tt.want)
			}
		})
	}
}
