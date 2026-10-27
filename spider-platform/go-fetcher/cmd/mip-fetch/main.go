// mip-fetch is the high-concurrency fetch worker of the Emithran MIP spider platform.
//
//	mip-fetch run --job 42 --api http://127.0.0.1:8000
//
// The Python worker launches one process per job (source.extraction.fetch_engine = "go"). It fetches
// what the job plan lists, posts raw pages to the platform for parsing/normalisation/storage, and
// exits 0 for succeeded/partial/cancelled jobs, 1 otherwise.
package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"emithran.com/mip/fetcher/internal/api"
	"emithran.com/mip/fetcher/internal/fetch"
	"emithran.com/mip/fetcher/internal/job"
)

var version = "dev"

func main() { os.Exit(run(os.Args[1:])) }

func run(args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: mip-fetch run --job N [--api URL] [--replay-dir DIR] | version")
		return 2
	}
	switch args[0] {
	case "version":
		fmt.Println(version)
		return 0
	case "run":
		return runJob(args[1:])
	default:
		fmt.Fprintf(os.Stderr, "unknown command %q\n", args[0])
		return 2
	}
}

func runJob(args []string) int {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	jobID := fs.Int("job", 0, "crawl job id (required)")
	base := fs.String("api", getenv("MIP_GO_FETCHER_API", "http://127.0.0.1:8000"), "control-plane base URL")
	replay := fs.String("replay-dir", os.Getenv("MIP_REPLAY_DIR"), "serve recorded responses from this directory instead of the network")
	_ = fs.Parse(args)
	if *jobID <= 0 {
		fmt.Fprintln(os.Stderr, "--job is required")
		return 2
	}

	log := slog.New(slog.NewTextHandler(os.Stdout, nil))
	opts := job.Options{JobID: *jobID, API: api.New(*base, os.Getenv("MIP_ADMIN_TOKEN")), Log: log}
	if *replay != "" {
		rt, err := fetch.NewReplay(*replay)
		if err != nil {
			log.Error("replay", "err", err)
			return 2
		}
		opts.Transport = rt
		log.Warn("REPLAY MODE: serving recorded responses, network is not used", "dir", *replay)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	status, err := job.Run(ctx, opts)
	if err != nil {
		log.Error("job failed", "err", err)
		return 1
	}
	switch status {
	case "succeeded", "partial", "cancelled":
		return 0
	}
	return 1
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
