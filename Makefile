.PHONY: fmt test build docker-up docker-down
fmt:
	gofmt -w $$(find . -name '*.go')
test:
	go test ./...
build:
	go build ./cmd/server
docker-up:
	docker compose up --build -d
docker-down:
	docker compose down
