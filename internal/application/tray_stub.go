package application

import (
	"context"

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
