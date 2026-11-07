---
title: 环境配置
---

- 请确保已准备好电子表格编辑软件，例如
  LibreOffice、Microsoft Excel 或 Google Sheets。

- 安装 R、RStudio 和所需的包（见下文）。

### R 和 RStudio

- R 和 RStudio 需要分别下载和安装。R 是底层的统计计算环境，
  但单独使用 R 并不有趣。RStudio 是一个图形化集成开发环境
  （IDE），让 R 的使用更加便捷，交互也更加方便。
  安装 RStudio 前，需要先安装 R。安装好这两个程序后，
  还需要在 RStudio 中安装一些特定的 R 包。
  请按照下方适用于您操作系统的说明进行操作，
  然后按照说明安装所需的包。

### Windows 用户

<br>

:::::::::::::::  solution

## 如果已经安装了 R 和 RStudio

- 打开 RStudio，点击 "Help" > "Check for updates"。如果有新版本，
  请退出 RStudio，然后下载最新版本的 RStudio。

- 要查看正在使用的 R 版本，请启动 RStudio，控制台最先显示的信息中
  会包含当前的 R 版本。也可以输入 `sessionInfo()`，
  该命令同样会显示正在运行的 R 版本。请访问
  [CRAN 网站](https://cran.r-project.org/bin/windows/base/)，查看是否有更新版本。
  如果有，请下载并安装。如果想从系统中删除旧版本，
  可以[在此查看](https://cran.r-project.org/bin/windows/base/rw-FAQ.html#How-do-I-UNinstall-R_003f)
  相关说明。

- 按照本页底部[所有用户](#for-everyone)一节中的步骤操作。


:::::::::::::::::::::::::

:::::::::::::::  solution

## 如果尚未安装 R 和 RStudio

- 从 [CRAN 网站](https://cran.r-project.org/bin/windows/base/release.htm)
  下载 R。

- 运行刚刚下载的 `.exe` 文件。

- 访问 [RStudio 下载页面](https://www.rstudio.com/products/rstudio/download/#download)。

- 在 *All Installers* 下选择 **RStudio xxxx.yy.zz-uuu.exe - Windows 10/11**（其中 x、y、z 和 u 表示版本号）。

- 双击该文件进行安装。

- 安装完成后，打开 RStudio，确认它可以正常运行，且没有出现错误信息。

- 按照本页底部[所有用户](#for-everyone)一节中的步骤操作。


:::::::::::::::::::::::::

### macOS 用户

<br>

:::::::::::::::  solution

## 如果已经安装了 R 和 RStudio

- 打开 RStudio，点击 "Help" > "Check for updates"。如果有新版本，
  请退出 RStudio，然后下载最新版本的 RStudio。

- 要查看正在使用的 R 版本，请启动 RStudio，终端最先显示的信息中
  会包含当前的 R 版本。也可以输入 `sessionInfo()`，
  该命令同样会显示正在运行的 R 版本。请访问
  [CRAN 网站](https://cran.r-project.org/bin/macosx/)，查看是否有更新版本。
  如果有，请下载并安装。

- 按照本页底部[所有用户](#for-everyone)一节中的步骤操作。


:::::::::::::::::::::::::

:::::::::::::::  solution

## 如果尚未安装 R 和 RStudio

- 从 [CRAN 网站](https://cran.r-project.org/bin/macosx/)
  下载 R。

- 选择最新 R 版本的 `.pkg` 文件。

- 双击下载的文件以安装 R。

- 还建议安装 [XQuartz](https://www.xquartz.org/)（某些包需要使用它）。

- 访问 [RStudio 下载页面](https://www.rstudio.com/products/rstudio/download/#download)。

- 在 *All Installers* 下选择 **RStudio xxxx.yy.zz-uuu.dmg - macOS 10.15+**（其中 x、y、z 和 u 表示版本号）。

- 双击该文件以安装 RStudio。

- 安装完成后，打开 RStudio，确认它可以正常运行，且没有出现错误信息。

- 按照本页底部[所有用户](#for-everyone)一节中的步骤操作。


:::::::::::::::::::::::::

### Linux 用户

<br>

:::::::::::::::  solution

## 使用包管理器安装 R，并安装 RStudio

- 请按照 [CRAN](https://cloud.r-project.org/bin/linux) 上适用于您发行版的说明操作。
  其中介绍了如何为常见发行版获取最新版本的 R。对于大多数发行版，
  也可以使用包管理器（例如，在 Debian/Ubuntu 上运行
  `sudo apt-get install r-base`，在 Fedora 上运行 `sudo yum install R`），
  但我们不推荐这种方法，因为通过这种方式提供的版本通常已经过时。
  无论使用哪种方式，请确保 R 版本至少为 4.2.0。
- 访问 [RStudio 下载页面](https://www.rstudio.com/products/rstudio/download/#download)。
- 在 *All Installers* 下选择与您发行版匹配的版本，
  然后使用您偏好的方式安装（例如，在 Debian/Ubuntu 上，
  可以在终端中运行 `sudo dpkg -i rstudio-xxxx.yy.zz-uuu-amd64.deb`）。
- 安装完成后，打开 RStudio，确认它可以正常运行，且没有出现错误信息。
- 按照[所有用户的说明](#for-everyone)操作。


:::::::::::::::::::::::::

### 所有用户 {#for-everyone}

安装 R 和 RStudio 后，还需要安装本次工作坊中使用的一些包。
我们也会在课程中学习如何安装包，并解释下列命令。
现在，请先按照以下说明操作：

- 双击图标启动 RStudio，然后输入：

```r
install.packages(c("BiocManager", "remotes"))
BiocManager::install(c("tidyverse", "SummarizedExperiment", "hexbin",
                       "patchwork", "gridExtra", "lubridate"))
```
