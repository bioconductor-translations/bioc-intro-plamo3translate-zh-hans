# Shared local and CI build customizations. All package changes live in memory.
copy_lesson_icons <- function(root) {
  # Varnish stores the Incubator favicons at asset root.
  assets <- system.file("pkgdown", "assets", package = "varnish")
  icons <- list.files(assets, full.names = TRUE,
                      pattern = "^(favicon|apple-touch-icon|android-chrome|mstile|site[.]webmanifest|safari-pinned-tab)")
  icons <- icons[!file.info(icons)$isdir]
  destination <- file.path(root, "site", "docs", "favicons", "incubator")
  dir.create(destination, recursive = TRUE, showWarnings = FALSE)
  if (!length(icons) || !all(file.copy(icons, destination, overwrite = TRUE))) {
    stop("Could not copy the bundled favicon assets")
  }
  manifest_path <- file.path(destination, "site.webmanifest")
  manifest <- jsonlite::read_json(manifest_path, simplifyVector = FALSE)
  manifest$icons <- lapply(manifest$icons, function(icon) {
    icon$src <- basename(icon$src)
    icon
  })
  jsonlite::write_json(manifest, manifest_path, auto_unbox = TRUE, pretty = TRUE)
}

with_workbench_customizations <- function(root, build) {
  ns <- asNamespace("sandpaper")
  replace_binding <- function(name, value) {
    unlockBinding(name, ns)
    assign(name, value, envir = ns)
    lockBinding(name, ns)
  }

  # Keep the configured HTML language while supplying the missing Chinese UI.
  ui <- jsonlite::read_json(file.path(root, "translation", "ui-zh-hans.json"),
                            simplifyVector = FALSE)
  translation_state <- get("these", ns, inherits = FALSE)
  original_translations <- translation_state$translations
  original_set_language <- get("set_language", ns, inherits = FALSE)
  replace_binding("set_language", function(lang = NULL) {
    if (!identical(lang, "zh-hans")) return(original_set_language(lang))
    original_set_language("en")
    for (group in c("varnish", "computed")) {
      keys <- names(translation_state$translations[[group]])
      missing <- setdiff(keys, names(ui[[group]]))
      if (length(missing)) {
        stop("Missing Simplified Chinese UI translations: ",
             paste(paste0(group, ".", missing), collapse = ", "))
      }
      translated <- lapply(keys, function(key) ui[[group]][[key]])
      names(translated) <- keys
      translation_state$translations[[group]] <- translated
    }
    invisible(NULL)
  })
  on.exit({
    replace_binding("set_language", original_set_language)
    translation_state$translations <- original_translations
  }, add = TRUE)

  # Resolve citations before lesson.lua transforms challenge/solution divs.
  original_pandoc_args <- get("construct_pandoc_args", ns, inherits = FALSE)
  bibliography <- normalizePath(file.path(root, "refs.bib"), mustWork = TRUE)
  replace_binding("construct_pandoc_args", function(path_in, output, to = "html", ...) {
    result <- original_pandoc_args(path_in, output, to, ...)
    if (to %in% c("html", "html4", "html5")) {
      result$options <- c("--citeproc", paste0("--bibliography=", bibliography),
                          result$options)
    }
    result
  })
  on.exit(replace_binding("construct_pandoc_args", original_pandoc_args), add = TRUE)

  # ci_deploy commits immediately after build_site returns. Copy assets here
  # so the same icons are included in both local builds and deployed commits.
  original_build_site <- get("build_site", ns, inherits = FALSE)
  replace_binding("build_site", function(...) {
    result <- original_build_site(...)
    copy_lesson_icons(root)
    result
  })
  on.exit(replace_binding("build_site", original_build_site), add = TRUE)

  build()
}
