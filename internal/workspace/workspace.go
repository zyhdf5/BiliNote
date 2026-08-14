package workspace

import (
	"os"
	"path/filepath"
	"time"
)

func New(root, taskID string) (string, error) {
	if err := os.MkdirAll(root, 0o755); err != nil {
		return "", err
	}
	return os.MkdirTemp(root, taskID+"-")
}
func CleanupStale(root string, age time.Duration) error {
	entries, err := os.ReadDir(root)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	now := time.Now()
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		info, er := e.Info()
		if er != nil {
			continue
		}
		if now.Sub(info.ModTime()) > age {
			_ = os.RemoveAll(filepath.Join(root, e.Name()))
		}
	}
	return nil
}
