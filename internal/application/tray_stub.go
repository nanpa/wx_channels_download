package application

import (
	"context"
	"net"
	"net/url"
	"os/exec"
	"runtime"
	"strconv"
	"strings"

	"wx_channel/internal/config"
	"wx_channel/internal/interceptor"
)

// The Python/Tkinter launcher owns the desktop window lifecycle. Keeping the
// native tray behind the with_system_tray build tag lets the Windows core build
// with CGO disabled and avoids requiring WebView2/C toolchains in CI.
func application_tray_supported() bool { return false }

func run_application_tray(
	ctx context.Context,
	stop context.CancelFunc,
	cfg *config.Config,
	page_url string,
	interceptor_server *interceptor.InterceptorServer,
	proxy_menu_available bool,
) {
	<-ctx.Done()
}

func application_page_url(protocol, hostname string, port int) string {
	scheme := strings.TrimSpace(protocol)
	if scheme == "" {
		scheme = "http"
	}
	host := strings.Trim(strings.TrimSpace(hostname), "[]")
	switch host {
	case "", "0.0.0.0":
		host = "127.0.0.1"
	case "::":
		host = "::1"
	}
	return (&url.URL{Scheme: scheme, Host: net.JoinHostPort(host, strconv.Itoa(port))}).String()
}

func open_external_url(rawURL string) error {
	var command *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		command = exec.Command("rundll32", "url.dll,FileProtocolHandler", rawURL)
	case "darwin":
		command = exec.Command("open", rawURL)
	default:
		command = exec.Command("xdg-open", rawURL)
	}
	if err := command.Start(); err != nil {
		return err
	}
	go func() { _ = command.Wait() }()
	return nil
}
