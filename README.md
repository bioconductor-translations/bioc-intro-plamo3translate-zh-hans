# Introduction to genomic data analysis with R and Bioconductor

## Contributing

We welcome all contributions to improve the lesson! Maintainers will
do their best to help you if you have any questions, concerns, or
experience any difficulties along the way.

We'd like to ask you to familiarize yourself with our [Contribution
Guide](CONTRIBUTING.md) and have a look at the [more detailed
guidelines][lesson-example] on proper formatting, ways to render the
lesson locally, and even how to write new episodes.

Please see the current list of [issues][FIXME] for ideas for
contributing to this repository. For making your contribution, we use
the GitHub flow, which is nicely explained in the chapter
[Contributing to a
Project](http://git-scm.com/book/en/v2/GitHub-Contributing-to-a-Project)
in Pro Git by Scott Chacon.

Look for the tag
![good\_first\_issue](https://img.shields.io/badge/-good%20first%20issue-gold.svg). This
indicates that the maintainers will welcome a pull request fixing this
issue.

## Useful links

- If you're going to be developing lesson material for the first time
  according to our design principles, consider reading the
  [Carpentries Curriculum Development Handbook][cdh]
- Consult the [Lesson Example][lesson-example] website to find out more about
  working with the lesson template

## Lesson team

This lesson has been developed and is current maintained by

- Laurent Gatto (maintainer)
- Charlotte Soneson
- Jenny Drnevich
- Robert Castelo
- Kevin Rue-Albert

We would also like to acknowledge the contributions of:

- Oliver Crook, Sarah Kaspar, Nick Hirschmueller, Lisa Breckels and Maria Doyle for their contributions during the Bioconductor introduction workshop in Heidelberg, as part of EuroBioc2021 |> 2022.
- Axelle Loriot, Marco Chiapelle, Manon Martin and Toby Hodges for various contributions and discussions.
- lmsimp, alorot, manonmartin, mchiapello, stavares843, JennyZadeh, csdaw, ninja-1337, fursham-h, lagerratrobe, fmichonneau, federicomarini, tobyhodges for pull requests.

If we have contributed but we missed you, apologies, and feel free to add yourself with a PR.

## Authors

A list of contributors to the lesson can be found in [AUTHORS](AUTHORS)

## Citation

To cite this lesson, please consult with [CITATION](CITATION)

See also: Drnevich, Jenny, Frederick J. Tan, Fabricio Almeida-Silva,
Robert Castelo, Aedin C. Culhane, Sean Davis, Maria A. Doyle, et
al. 2025. [Learning and Teaching Biological Data Science in the
Bioconductor
Community](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1012925). PLoS
Computational Biology 21 (4): e1012925.


## Updating the Simplified Chinese episodes

The translation tool reads the English episodes from `upstream/main`, reuses
compatible Simplified Chinese translation memory, and translates changed prose
with the pinned PLaMo model. The target language is `zh-hans`.

Configure `upstream` to use the English lesson repository. If it does not exist,
add it with `git remote add upstream https://github.com/carpentries-incubator/bioc-intro.git`.
If it currently points to a translated fork, update it before fetching:

```sh
git remote set-url upstream https://github.com/carpentries-incubator/bioc-intro.git
```

Then preview and apply the translation update:

```sh
git fetch upstream main
uv run --locked scripts/translate.py --source-ref upstream/main --dry-run
uv run --locked scripts/translate.py --source-ref upstream/main --update-memory
```

See [the translation guide](translation/README.md) for setup, offline replay,
reviewing new translations, and protecting local edits. Machine-generated
Simplified Chinese translations need review before they are marked `reviewed`.

## Testing locally

The translation tests need Python 3.12 only; they do not load the model or use
the network:

```sh
python3.12 -m unittest discover -s tests -v
```

To build the translated site with Simplified Chinese navigation, bibliography
citations, and theme icons, run:

```sh
Rscript scripts/build.R
```

The rendered site is written to `site/docs/`. Add `--rebuild` to regenerate
all lesson code output. Use this build script to apply the bundled Simplified
Chinese navigation and interface labels from `translation/ui-zh-hans.json`;
the installed Workbench packages do not include this language.
The build-and-deploy workflow uses `scripts/ci-build.R` with the same Chinese
labels, citation processing, and theme icons before the standard deployment.

To preview the generated site locally, run the following in the lesson directory:

```sh
python3 -m http.server --directory site/docs 8000
```

Open <http://localhost:8000> in a browser. Re-run `Rscript scripts/build.R` after
editing lesson content so the bundled Chinese labels remain applied.

For more details, see the [workbench installation
instructions](https://carpentries.github.io/workbench/#installation).
