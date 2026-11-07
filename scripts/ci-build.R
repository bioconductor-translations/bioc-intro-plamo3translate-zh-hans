#!/usr/bin/env Rscript
# Use the standard Carpentries deployment pipeline with this lesson's UI fixes.
# Package-override setup follows carpentries/actions/build-and-deploy@v1.
install_workbench_overrides <- function(config) {
  for (key in c("varnish", "sandpaper", "pegboard")) {
    ref <- config[[key]]
    if (is.null(ref)) next
    ref <- gsub("[\"']", "", trimws(as.character(ref)))
    if (grepl("^[0-9]", ref)) ref <- paste0("carpentries/", key, "@", ref)
    if (ref == "latest") next
    cat("::group::Installing", ref, "\n")
    remotes::install_github(ref)
    cat("::endgroup::\n")
  }
}

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  if ("--help" %in% args) {
    cat("Usage: Rscript scripts/ci-build.R\n",
        "Set BUILD_RESET=true or SKIP_MANAGE_DEPS=true to enable those CI options.\n",
        sep = "")
    return(invisible(NULL))
  }
  if (length(args)) stop("Unknown CI build argument")
  script <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE))
  if (length(script) != 1L) stop("Run this script with Rscript")
  root <- dirname(dirname(normalizePath(script, mustWork = TRUE)))

  # Enable gettext in this process, matching the local build entrypoint.
  Sys.unsetenv("LC_ALL")
  install_workbench_overrides(yaml::read_yaml(file.path(root, "config.yaml")))
  source(file.path(root, "scripts", "workbench-customizations.R"), local = TRUE)
  sandpaper::package_cache_trigger(TRUE)
  with_workbench_customizations(root, function() {
    sandpaper:::ci_deploy(
      path = root,
      reset = identical(Sys.getenv("BUILD_RESET"), "true"),
      skip_manage_deps = identical(Sys.getenv("SKIP_MANAGE_DEPS"), "true")
    )
  })
}

main()
