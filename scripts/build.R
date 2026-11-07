#!/usr/bin/env Rscript
# Build the translated lesson with the installed Carpentries Workbench packages.
main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  if ("--help" %in% args) {
    cat("Usage: Rscript scripts/build.R [--rebuild]\n")
    return(invisible(NULL))
  }
  if (length(setdiff(args, "--rebuild"))) stop("Unknown build argument")
  script <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE))
  if (length(script) != 1L) stop("Run this script with Rscript")
  root <- dirname(dirname(normalizePath(script, mustWork = TRUE)))

  # LC_ALL=C.UTF-8 prevents gettext from selecting the translated theme strings.
  # This affects this build process only; no shell or system settings are changed.
  Sys.unsetenv("LC_ALL")
  packages <- c("sandpaper", "rmarkdown", "varnish", "jsonlite")
  missing <- packages[!vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
  if (length(missing)) stop("Missing R packages: ", paste(missing, collapse = ", "))

  pandoc <- rmarkdown::find_pandoc(cache = FALSE)
  usable <- function(p) nzchar(p$dir) && p$version >= "2.11"
  if (!usable(pandoc)) {
    for (dir in c("/Applications/quarto/bin/tools/aarch64",
                  "/Applications/quarto/bin/tools/x86_64",
                  "/Applications/quarto/bin/tools")) {
      if (!file.exists(file.path(dir, "pandoc"))) next
      candidate <- rmarkdown::find_pandoc(cache = FALSE, dir = dir)
      if (usable(candidate)) {
        pandoc <- candidate
        break
      }
    }
  }
  if (!usable(pandoc)) stop("Pandoc 2.11 or newer is required")
  Sys.setenv(RSTUDIO_PANDOC = pandoc$dir)

  readme <- file.path(root, "site", "README.md")
  if (file.exists(readme)) {
    original_readme <- readBin(readme, "raw", n = file.info(readme)$size)
    on.exit({
      dir.create(dirname(readme), recursive = TRUE, showWarnings = FALSE)
      writeBin(original_readme, readme)
    }, add = TRUE)
  }
  previous_options <- options(sandpaper.use_renv = FALSE)
  on.exit(options(previous_options), add = TRUE)

  source(file.path(root, "scripts", "workbench-customizations.R"), local = TRUE)
  with_workbench_customizations(root, function() {
    sandpaper::build_lesson(path = root, rebuild = "--rebuild" %in% args,
                           quiet = FALSE, preview = FALSE)
  })
  cat("\nBuilt site: ", file.path(root, "site", "docs", "index.html"), "\n", sep = "")
}

main()
