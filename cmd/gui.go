package cmd

import (
	"github.com/spf13/cobra"

	"wx_channel/internal/application"
)

var gui_no_open bool

var gui_cmd = &cobra.Command{
	Use:   "gui",
	Short: "启动图形管理界面",
	Long:  "启动下载与代理服务，打开图形管理界面，并在系统托盘中保持运行。",
	RunE: func(cmd *cobra.Command, args []string) error {
		if start_transferred {
			return nil
		}
		return application.StartGUI(Cfg, application.GUIOptions{
			OpenBrowser: !gui_no_open,
		})
	},
}

func init() {
	gui_cmd.Flags().BoolVar(&gui_no_open, "no-open", false, "启动后不自动打开管理界面")
	root_cmd.AddCommand(gui_cmd)
}
