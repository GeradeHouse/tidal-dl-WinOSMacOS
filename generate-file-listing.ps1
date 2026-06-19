# generate-file-listing.ps1
# Generate File Listing Script
# Creates a text file containing all project files, excluding paths that match current .gitignore rules.
# Optional file selection mode allows listing only a pasted/typed subset after the same exclusion filtering is applied.
# Preferred implementation uses `git ls-files` for discovery, then applies a .gitignore-style export filter.
# Falls back to best-effort filesystem discovery with the same .gitignore matcher if Git/worktree is unavailable.
# After writing the output file, copies its full content to the Windows clipboard (best-effort).

param(
    [string]$OutputFile = "project-files-listing.txt",
    [string]$ProjectPath = ".",

    # Remove older timestamped listing snapshots so only the newly generated listing remains
    [switch]$CleanPreviousListings = $false,

    # Top-level folders to expand in the overview.
    # Use "*" to expand every folder into a full tree.
    [string[]]$ExpandOverviewFolders = @("*"),

    # Clipboard copying is temporarily disabled.
    # Keep the parameter for backward compatibility, but do not invoke clipboard copying in Main.
    [bool]$CopyToClipboard = $false,

    # Enable file selection mode (only list selected files; skips the project structure overview)
    [switch]$SelectFiles = $false,

    # Optional pre-supplied selection text (skips prompt; same parsing rules as interactive/clipboard input)
    [string]$SelectFilesText = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-TimestampedOutputPath {
    param([Parameter(Mandatory)][string]$Path)

    $timestamp = Get-Date -Format "ddMMyy-HHmmss"
    $directory = [System.IO.Path]::GetDirectoryName($Path)
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $extension = [System.IO.Path]::GetExtension($Path)

    if ([string]::IsNullOrWhiteSpace($baseName)) {
        $baseName = $Path
    }

    $timestampedFileName = if ([string]::IsNullOrEmpty($extension)) {
        "${baseName}_${timestamp}"
    } else {
        "${baseName}_${timestamp}${extension}"
    }

    if ([string]::IsNullOrWhiteSpace($directory)) {
        return $timestampedFileName
    }

    return Join-Path $directory $timestampedFileName
}

function Add-ProjectNameToOutputPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$ProjectRootPath
    )

    $directory = [System.IO.Path]::GetDirectoryName($Path)
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $extension = [System.IO.Path]::GetExtension($Path)

    if ([string]::IsNullOrWhiteSpace($baseName)) {
        $baseName = $Path
    }

    $projectName = [System.IO.Path]::GetFileName($ProjectRootPath.TrimEnd('\','/'))
    if (-not [string]::IsNullOrWhiteSpace($projectName)) {
        $expectedPrefix = "${projectName}_"
        if (-not $baseName.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $baseName = "${projectName}_${baseName}"
        }
    }

    $prefixedFileName = if ([string]::IsNullOrEmpty($extension)) {
        $baseName
    } else {
        "${baseName}${extension}"
    }

    if ([string]::IsNullOrWhiteSpace($directory)) {
        return $prefixedFileName
    }

    return Join-Path $directory $prefixedFileName
}

function Normalize-RelPath {
    param([Parameter(Mandatory)][string]$Path)

    $p = $Path -replace '\\', '/'
    while ($p.StartsWith("./")) { $p = $p.Substring(2) }
    return $p.TrimStart('/')
}

function Get-RepoRootIfAvailable {
    param([Parameter(Mandatory)][string]$Path)

    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { return $null }

    try {
        $root = & git -C $Path rev-parse --show-toplevel 2>$null
        if (-not $root) { return $null }
        $root = ($root | Select-Object -First 1).Trim()
        if ([string]::IsNullOrWhiteSpace($root)) { return $null }
        return $root
    }
    catch {
        return $null
    }
}

function Get-GitTrackedAndUntrackedNotIgnored {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$ResolvedProjectPath
    )

    $paths = & git -C $RepoRoot ls-files --cached --others --exclude-standard 2>$null
    if (-not $paths) { return @() }

    $repoRootNorm = (Resolve-Path $RepoRoot).Path.TrimEnd('\','/')
    $projNorm = (Resolve-Path $ResolvedProjectPath).Path.TrimEnd('\','/')

    $subPrefix = ""
    if ($projNorm.Length -gt $repoRootNorm.Length) {
        $sub = $projNorm.Substring($repoRootNorm.Length).TrimStart('\','/')
        $subPrefix = (Normalize-RelPath $sub).TrimEnd('/')
        if ($subPrefix.Length -gt 0) { $subPrefix = $subPrefix + "/" }
    }

    $outFileNorm = Normalize-RelPath $OutputFile

    $result = New-Object System.Collections.Generic.List[object]
    foreach ($p in $paths) {
        if ([string]::IsNullOrWhiteSpace($p)) { continue }

        $pNorm = Normalize-RelPath $p
        if ($pNorm -like ".git/*") { continue }

        if ($subPrefix.Length -gt 0) {
            if (-not $pNorm.StartsWith($subPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
            $relToProject = $pNorm.Substring($subPrefix.Length)
        } else {
            $relToProject = $pNorm
        }

        if ($relToProject.Equals($outFileNorm, [System.StringComparison]::OrdinalIgnoreCase)) { continue }

        $fullPath = Join-Path $RepoRoot $pNorm
        if (-not (Test-Path -LiteralPath $fullPath)) { continue }

        $result.Add([pscustomobject]@{
            FullPath     = $fullPath
            RelativePath = $relToProject
        })
    }

    return $result | Sort-Object RelativePath
}

function Get-GitIgnorePatterns {
    param([Parameter(Mandatory)][string]$ProjectPath)

    $gitIgnorePath = Join-Path $ProjectPath ".gitignore"
    if (-not (Test-Path -LiteralPath $gitIgnorePath)) { return @() }

    $lines = @(Get-Content -LiteralPath $gitIgnorePath -ErrorAction Stop)

    if ($null -eq $lines -or $lines.Count -eq 0) {
        return @()
    }

    $normalized = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if ($null -eq $line) { continue }
        $normalized.Add([string]$line)
    }

    return ,$normalized.ToArray()
}

function Convert-GitIgnorePatternToRegex {
    param([Parameter(Mandatory)][string]$Pattern)

    # ** -> .*
    # *  -> [^/]*   (does not cross /)
    # ?  -> [^/]
    $sb = New-Object System.Text.StringBuilder
    for ($i = 0; $i -lt $Pattern.Length; $i++) {
        $ch = $Pattern[$i]
        if ($ch -eq '*') {
            if (($i + 1) -lt $Pattern.Length -and $Pattern[$i + 1] -eq '*') {
                [void]$sb.Append('.*')
                $i++
            } else {
                [void]$sb.Append('[^/]*')
            }
            continue
        }
        if ($ch -eq '?') {
            [void]$sb.Append('[^/]')
            continue
        }

        if ($ch -match '[\\\.\+\(\)\{\}\[\]\^\$\|]') {
            [void]$sb.Append('\')
        }
        [void]$sb.Append($ch)
    }
    return $sb.ToString()
}

function Compile-GitIgnoreRules {
    param(
        [AllowNull()]
        [AllowEmptyCollection()]
        [string[]]$GitIgnoreLines = @()
    )

    $rules = New-Object System.Collections.Generic.List[object]

    foreach ($rawLine in $GitIgnoreLines) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line.StartsWith('#')) { continue }

        $isNegation = $line.StartsWith('!')
        if ($isNegation) { $line = $line.Substring(1) }

        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }

        $anchored = $line.StartsWith('/')
        if ($anchored) { $line = $line.Substring(1) }

        $directoryOnly = $line.EndsWith('/')
        if ($directoryOnly) { $line = $line.TrimEnd('/') }

        $coreRegex = Convert-GitIgnorePatternToRegex -Pattern $line

        $prefix = $anchored ? '^' : '(?:^|.*/)'
        $suffix = '(?:$|/.*)$'
        $fullRegex = $prefix + $coreRegex + $suffix

        $rules.Add([pscustomobject]@{
            IsNegation = $isNegation
            Regex      = [regex]::new($fullRegex, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
            Raw        = $rawLine
        })
    }

    return $rules.ToArray()
}

function Test-IsIgnoredByRules {
    param(
        [Parameter(Mandatory)][string]$RelativePath,
        [AllowNull()][object[]]$Rules
    )

    $p = Normalize-RelPath $RelativePath
    if ($p -like ".git/*") { return $true }

    if ($null -eq $Rules -or $Rules.Count -eq 0) {
        return $false
    }

    $flatRules = New-Object System.Collections.Generic.List[object]
    foreach ($entry in $Rules) {
        if ($null -eq $entry) { continue }

        $hasRegex = ($null -ne $entry.PSObject.Properties['Regex'])
        $isDict = ($entry -is [System.Collections.IDictionary])
        $isListLike = ($entry -is [System.Collections.IEnumerable]) -and -not ($entry -is [string]) -and -not $hasRegex -and -not $isDict

        if ($isListLike) {
            foreach ($nested in $entry) {
                if ($null -ne $nested) { $flatRules.Add($nested) }
            }
        } else {
            $flatRules.Add($entry)
        }
    }

    if ($flatRules.Count -eq 0) {
        return $false
    }

    $ignored = $false
    foreach ($r in $flatRules) {
        if ($null -eq $r) { continue }
        if ($null -eq $r.PSObject.Properties['Regex']) { continue }
        if ($null -eq $r.PSObject.Properties['IsNegation']) { continue }

        if ($r.Regex.IsMatch($p)) {
            $ignored = -not [bool]$r.IsNegation
        }
    }

    return $ignored
}

function Get-AllProjectFilesFallback {
    param(
        [Parameter(Mandatory)][string]$ProjectPath,
        [AllowNull()]
        [AllowEmptyCollection()]
        [object[]]$Rules = @()
    )

    $root = (Resolve-Path $ProjectPath).Path.TrimEnd('\','/')
    $outFileNorm = Normalize-RelPath $OutputFile

    $files = Get-ChildItem -LiteralPath $root -File -Recurse -Force -ErrorAction SilentlyContinue
    $result = New-Object System.Collections.Generic.List[object]

    foreach ($f in $files) {
        if (($f.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }

        $rel = $f.FullName.Substring($root.Length).TrimStart('\','/')
        $rel = Normalize-RelPath $rel

        if ($rel.Equals($outFileNorm, [System.StringComparison]::OrdinalIgnoreCase)) { continue }

        if (-not (Test-IsIgnoredByRules -RelativePath $rel -Rules $Rules)) {
            $result.Add([pscustomobject]@{
                FullPath     = $f.FullName
                RelativePath = $rel
            })
        }
    }

    return $result | Sort-Object RelativePath
}

function Get-FileTextAndLineCount {
    param([Parameter(Mandatory)][string]$FilePath)

    try {
        $fs = [System.IO.File]::Open($FilePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            $buf = New-Object byte[] 4096
            $read = $fs.Read($buf, 0, $buf.Length)
            for ($i = 0; $i -lt $read; $i++) {
                if ($buf[$i] -eq 0) {
                    return @{ Content = "# [binary file omitted]"; LineCount = 1 }
                }
            }
        }
        finally { $fs.Dispose() }

        $content = Get-Content -LiteralPath $FilePath -Raw -ErrorAction Stop
        if ($null -eq $content) { $content = "" }

        $lines = if ($content.Length -eq 0) { 0 } else { ([regex]::Matches($content, "`n")).Count + 1 }
        return @{ Content = $content; LineCount = $lines }
    }
    catch {
        return @{ Content = ""; LineCount = 1 }
    }
}

function Convert-ContentToNumberedLines {
    param(
        [AllowNull()]
        [string]$Content,

        [Parameter(Mandatory)]
        [int]$LineCount
    )

    if ($null -eq $Content) {
        $Content = ""
    }

    if ($Content.Length -eq 0) {
        return ""
    }

    # Normalize only for generated listing output.
    # This avoids mixed CRLF/LF rendering while preserving line boundaries.
    $normalized = $Content -replace "`r`n", "`n"
    $normalized = $normalized -replace "`r", "`n"

    $lines = $normalized.Split("`n")
    if ($lines.Count -gt 1 -and $normalized.EndsWith("`n")) {
        $lines = $lines[0..($lines.Count - 2)]
    }

    $width = [Math]::Max(1, ([string][Math]::Max($LineCount, $lines.Count)).Length)

    $sb = New-Object System.Text.StringBuilder

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $lineNumber = ($i + 1).ToString().PadLeft($width)
        [void]$sb.Append($lineNumber)
        [void]$sb.Append(" | ")
        [void]$sb.AppendLine($lines[$i])
    }

    return $sb.ToString()
}

function Get-TextPhysicalLineCount {
    param(
        [AllowNull()]
        [string]$Text
    )

    if ([string]::IsNullOrEmpty($Text)) {
        return 0
    }

    $normalized = $Text -replace "`r`n", "`n"
    $normalized = $normalized -replace "`r", "`n"

    $newlineCount = ([regex]::Matches($normalized, "`n")).Count

    if ($normalized.EndsWith("`n")) {
        return $newlineCount
    }

    return ($newlineCount + 1)
}

function Get-FileListingBlockLineCount {
    param(
        [Parameter(Mandatory)]
        $FileItem
    )

    $numberedContent = Convert-ContentToNumberedLines -Content $FileItem.Content -LineCount $FileItem.LineCount

    $bodyLineCount = if ([string]::IsNullOrEmpty($numberedContent)) {
        1
    } else {
        Get-TextPhysicalLineCount -Text $numberedContent
    }

    # Per file block:
    # 1. path header
    # 2. opening ```
    # 3. numbered content or one blank line
    # 4. closing ```
    # 5. blank separator line
    return (1 + 1 + $bodyLineCount + 1 + 1)
}

function Get-OverviewFileSuffix {
    param(
        [Parameter(Mandatory)]
        $FileInfo
    )

    $sourceLineCount = 0
    $listingStartLine = 0
    $listingEndLine = 0

    if ($null -ne $FileInfo.PSObject.Properties['LineCount']) {
        $sourceLineCount = [int]$FileInfo.LineCount
    }

    if ($null -ne $FileInfo.PSObject.Properties['ListingStartLine']) {
        $listingStartLine = [int]$FileInfo.ListingStartLine
    }

    if ($null -ne $FileInfo.PSObject.Properties['ListingEndLine']) {
        $listingEndLine = [int]$FileInfo.ListingEndLine
    }

    $listingPart = if ($listingStartLine -gt 0 -and $listingEndLine -ge $listingStartLine) {
        "listing: $listingStartLine-$listingEndLine"
    } else {
        "listing: pending"
    }

    return " [$listingPart; source: $sourceLineCount lines]"
}

function Set-GeneratedListingLineRanges {
    param(
        [Parameter(Mandatory)]
        [object[]]$Files,

        [Parameter(Mandatory)]
        [int]$FirstFileStartLine
    )

    $currentLine = $FirstFileStartLine

    foreach ($f in $Files) {
        $blockLineCount = Get-FileListingBlockLineCount -FileItem $f

        $f | Add-Member -NotePropertyName ListingStartLine -NotePropertyValue $currentLine -Force
        $f | Add-Member -NotePropertyName ListingEndLine -NotePropertyValue ($currentLine + $blockLineCount - 1) -Force

        $currentLine += $blockLineCount
    }
}

function New-TreeNode {
    return @{
        Dirs  = @{}  # name -> node
        Files = @{}  # name -> lineCount
    }
}

function Build-TreeFromPaths {
    param([Parameter(Mandatory)][object[]]$FileItems)

    $root = New-TreeNode

    foreach ($item in $FileItems) {
        $p = $item.RelativePath
        if ([string]::IsNullOrWhiteSpace($p)) { continue }
        $rel = Normalize-RelPath $p
        if ([string]::IsNullOrWhiteSpace($rel)) { continue }

        $fileInfo = [pscustomobject]@{
            LineCount        = $item.LineCount
            ListingStartLine = if ($null -ne $item.PSObject.Properties['ListingStartLine']) { $item.ListingStartLine } else { 0 }
            ListingEndLine   = if ($null -ne $item.PSObject.Properties['ListingEndLine']) { $item.ListingEndLine } else { 0 }
        }

        $parts = $rel.Split('/')
        if ($parts.Length -eq 1) {
            $root.Files[$parts[0]] = $fileInfo
            continue
        }

        $cur = $root
        for ($i = 0; $i -lt ($parts.Length - 1); $i++) {
            $d = $parts[$i]
            if ([string]::IsNullOrWhiteSpace($d)) { continue }

            if (-not $cur.Dirs.ContainsKey($d)) {
                $cur.Dirs[$d] = (New-TreeNode)
            }
            $cur = $cur.Dirs[$d]
        }

        $leaf = $parts[$parts.Length - 1]
        if (-not [string]::IsNullOrWhiteSpace($leaf)) {
            $cur.Files[$leaf] = $fileInfo
        }
    }

    return $root
}

function Get-TreeLines {
    param(
        [Parameter(Mandatory)]$Node,
        [Parameter(Mandatory)][string]$Indent
    )

    $out = New-Object System.Collections.Generic.List[string]

    foreach ($dirName in ($Node.Dirs.Keys | Sort-Object)) {
        $out.Add("$Indent- $dirName/")
        $childLines = Get-TreeLines -Node $Node.Dirs[$dirName] -Indent ($Indent + "  ")
        foreach ($cl in $childLines) { $out.Add($cl) }
    }

    foreach ($fileName in ($Node.Files.Keys | Sort-Object)) {
        $suffix = Get-OverviewFileSuffix -FileInfo $Node.Files[$fileName]
        $out.Add("$Indent- $fileName$suffix")
    }

    return ,$out.ToArray()
}

function Get-ProjectOverviewLines {
    param(
        [Parameter(Mandatory)][object[]]$Files,
        [Parameter(Mandatory)][string[]]$ExpandFolders
    )

    $counts = @{}
    $isFolder = @{}
    $topLevelFiles = @{}

    foreach ($f in $Files) {
        $rel = Normalize-RelPath $f.RelativePath
        if ([string]::IsNullOrWhiteSpace($rel)) { continue }

        $parts = $rel.Split('/', 2)
        $top = $parts[0]

        if (-not $counts.ContainsKey($top)) { $counts[$top] = 0 }
        $counts[$top]++

        if ($parts.Length -gt 1) {
            $isFolder[$top] = $true
        } else {
            if (-not $isFolder.ContainsKey($top)) { $isFolder[$top] = $false }
            $topLevelFiles[$top] = $f
        }
    }

    $expandSet = New-Object System.Collections.Generic.HashSet[string] ([System.StringComparer]::OrdinalIgnoreCase)
    $expandAll = $false

    foreach ($e in $ExpandFolders) {
        if ([string]::IsNullOrWhiteSpace($e)) { continue }

        $trimmed = $e.Trim()
        if ($trimmed -eq "*") {
            $expandAll = $true
            continue
        }

        [void]$expandSet.Add($trimmed)
    }

    $lines = New-Object System.Collections.Generic.List[string]
    $tops = $counts.Keys | Sort-Object

    for ($i = 0; $i -lt $tops.Count; $i++) {
        $name = $tops[$i]
        $count = $counts[$name]

        if ($isFolder[$name]) {
            $lines.Add("- $name/ ($count files)")

            if ($expandAll -or $expandSet.Contains($name)) {
                $prefix = $name + "/"
                $sub = New-Object System.Collections.Generic.List[object]

                foreach ($f in $Files) {
                    $rel = Normalize-RelPath $f.RelativePath
                    if ($rel.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                        $sub.Add([pscustomobject]@{
                            RelativePath      = $rel.Substring($prefix.Length)
                            LineCount         = $f.LineCount
                            ListingStartLine  = if ($null -ne $f.PSObject.Properties['ListingStartLine']) { $f.ListingStartLine } else { 0 }
                            ListingEndLine    = if ($null -ne $f.PSObject.Properties['ListingEndLine']) { $f.ListingEndLine } else { 0 }
                        })
                    }
                }

                $tree = Build-TreeFromPaths -FileItems ($sub.ToArray())
                $treeLines = Get-TreeLines -Node $tree -Indent "  "
                foreach ($tl in $treeLines) { $lines.Add($tl) }
            }
        } else {
            $suffix = if ($topLevelFiles.ContainsKey($name)) {
                Get-OverviewFileSuffix -FileInfo $topLevelFiles[$name]
            } else {
                ""
            }

            $lines.Add("- $name$suffix")
        }

        if ($i -ne ($tops.Count - 1)) {
            $lines.Add("")
        }
    }

    return ,$lines.ToArray()
}

function Get-ClipboardTextRobust {
    # Attempt 1: Get-Clipboard (preferred)
    try {
        $getClipboard = Get-Command Get-Clipboard -ErrorAction SilentlyContinue
        if ($getClipboard) {
            $t = Get-Clipboard -Raw
            if ($null -ne $t) { return [string]$t }
        }
    } catch {
        # fall through
    }

    # Attempt 2: System.Windows.Forms.Clipboard via STA thread
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop

        $state = [hashtable]::Synchronized(@{ Text = "" })

        $threadStart = [System.Threading.ParameterizedThreadStart]{
            param($s)
            try {
                $s.Text = [string][System.Windows.Forms.Clipboard]::GetText()
            } catch {
                $s.Text = ""
            }
        }

        $t = New-Object System.Threading.Thread($threadStart)
        $t.SetApartmentState([System.Threading.ApartmentState]::STA)
        $t.Start($state)
        $t.Join()

        if (-not [string]::IsNullOrWhiteSpace($state.Text)) { return [string]$state.Text }
    } catch {
        # fall through
    }

    return ""
}

function Set-ClipboardTextRobust {
    param([Parameter(Mandatory)][string]$Text)

    # Attempt 1: Set-Clipboard (preferred)
    try {
        $setClipboard = Get-Command Set-Clipboard -ErrorAction SilentlyContinue
        if ($setClipboard) {
            Set-Clipboard -Value $Text
            return $true
        }
    } catch {
        # fall through
    }

    # Attempt 2: System.Windows.Forms.Clipboard via STA thread
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop

        $threadStart = [System.Threading.ParameterizedThreadStart]{
            param($s)
            [System.Windows.Forms.Clipboard]::SetText([string]$s)
        }

        $t = New-Object System.Threading.Thread($threadStart)
        $t.SetApartmentState([System.Threading.ApartmentState]::STA)
        $t.Start($Text)
        $t.Join()
        return $true
    } catch {
        # fall through
    }

    # Attempt 3: clip.exe fallback (Windows)
    try {
        if ($env:SystemRoot) {
            $clipPath = Join-Path $env:SystemRoot "System32\clip.exe"
            if (Test-Path -LiteralPath $clipPath) {
                $prev = [Console]::OutputEncoding
                try {
                    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
                    $Text | & $clipPath | Out-Null
                } finally {
                    [Console]::OutputEncoding = $prev
                }

                if ($LASTEXITCODE -eq 0) { return $true }
            }
        }
    } catch {
        # fall through
    }

    return $false
}

function TryCopyFileToClipboard {
    param([Parameter(Mandatory)][string]$FilePath)

    try {
        if (-not (Test-Path -LiteralPath $FilePath)) {
            Write-Warning "Clipboard copy skipped: output file not found: $FilePath"
            return $false
        }

        # Read the file content back (ensures we copy what was actually written)
        $text = [System.IO.File]::ReadAllText($FilePath)

        if ([string]::IsNullOrEmpty($text)) {
            Write-Warning "Clipboard copy skipped: output file is empty."
            return $false
        }

        if (Set-ClipboardTextRobust -Text $text) {
            Write-Host "Copied output content to clipboard." -ForegroundColor Green
            return $true
        }

        Write-Warning "Failed to copy output content to clipboard (clipboard unavailable or session is non-interactive)."
        return $false
    } catch {
        Write-Warning ("Failed to copy output content to clipboard: " + $_.Exception.Message)
        return $false
    }
}

function Remove-PreviousListingSnapshots {
    param(
        [Parameter(Mandatory)][string]$ProjectRootPath,
        [Parameter(Mandatory)][string]$OutputFileTemplate,
        [Parameter(Mandatory)][string]$KeepFilePath
    )

    $templateDirectory = [System.IO.Path]::GetDirectoryName($OutputFileTemplate)
    $templateFileName = [System.IO.Path]::GetFileName($OutputFileTemplate)
    $templateBaseName = [System.IO.Path]::GetFileNameWithoutExtension($templateFileName)
    $templateExtension = [System.IO.Path]::GetExtension($templateFileName)

    if ([string]::IsNullOrWhiteSpace($templateBaseName)) {
        return 0
    }

    $searchRoot = if ([string]::IsNullOrWhiteSpace($templateDirectory)) {
        $ProjectRootPath
    } elseif ([System.IO.Path]::IsPathRooted($templateDirectory)) {
        $templateDirectory
    } else {
        Join-Path $ProjectRootPath $templateDirectory
    }

    if (-not (Test-Path -LiteralPath $searchRoot)) {
        return 0
    }

    $filter = if ([string]::IsNullOrWhiteSpace($templateExtension)) {
        "${templateBaseName}_*"
    } else {
        "${templateBaseName}_*${templateExtension}"
    }

    $nameRegex = if ([string]::IsNullOrWhiteSpace($templateExtension)) {
        '^' + [regex]::Escape($templateBaseName) + '_\d{6}-\d{6}$'
    } else {
        '^' + [regex]::Escape($templateBaseName) + '_\d{6}-\d{6}' + [regex]::Escape($templateExtension) + '$'
    }

    $keepPathResolved = $KeepFilePath
    try {
        $keepPathResolved = (Resolve-Path -LiteralPath $KeepFilePath -ErrorAction Stop).Path
    }
    catch {
        $keepPathResolved = $KeepFilePath
    }

    $removedCount = 0
    $candidates = Get-ChildItem -LiteralPath $searchRoot -File -Filter $filter -ErrorAction SilentlyContinue

    foreach ($candidate in $candidates) {
        if ($candidate.Name -notmatch $nameRegex) { continue }

        if ($candidate.FullName.Equals($keepPathResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }

        Remove-Item -LiteralPath $candidate.FullName -Force -ErrorAction Stop
        $removedCount++
    }

    return $removedCount
}

function Read-FileSelectionTextInteractive {
    # Selection editor:
    # - Paste via terminal (right-click / Ctrl+Shift+V) to insert multiline content (blank lines preserved).
    # - Enter inserts a newline in the editor (never submits).
    # - Tab toggles focus between editor and footer actions.
    # - Ctrl+Enter submits immediately from any focus.
    # - Footer actions: Edit (return focus), Clear input, Finish.
    # - F2 inserts clipboard contents explicitly (optional convenience; does not auto-submit).
    # - Esc cancels selection mode (Ctrl+C also cancels as usual).

    $rawUi = $null
    try { $rawUi = $Host.UI.RawUI } catch { $rawUi = $null }

    if ([Console]::IsInputRedirected) {
        try {
            $txt = [Console]::In.ReadToEnd()
            if ($null -eq $txt) { return "" }
            return [string]$txt
        } catch {
            return ""
        }
    }

    if (-not $rawUi) {
        return ""
    }

    $origin = $rawUi.CursorPosition
    $footerLines = 2
    $renderedLines = 0

    $historyEntries = @()
    try {
        $historyEntries = @(Get-History | Sort-Object Id | ForEach-Object { $_.CommandLine })
    } catch {
        $historyEntries = @()
    }

    $state = [ordered]@{
        Origin           = $origin
        FooterLines      = $footerLines
        RenderedLines    = $renderedLines

        Lines            = (New-Object System.Collections.Generic.List[string])
        CursorLine       = 0
        CursorCol        = 0

        Focus            = "Input" # Input | Footer
        ActionIndex      = 2       # 0=Edit, 1=Clear input, 2=Finish
        ViewTop          = 0

        MultiLineEngaged = $false

        HistoryEntries   = $historyEntries
        HistoryPos       = $historyEntries.Count
        HistoryOriginal  = ""
    }

    [void]$state.Lines.Add("")

    $ctrlMask = ([System.Management.Automation.Host.ControlKeyStates]::LeftCtrlPressed -bor [System.Management.Automation.Host.ControlKeyStates]::RightCtrlPressed)
    $shiftMask = ([System.Management.Automation.Host.ControlKeyStates]::ShiftPressed)

    function Get-IsCtrlPressed {
        param([Parameter(Mandatory)]$KeyInfo)
        return (($KeyInfo.ControlKeyState -band $ctrlMask) -ne 0)
    }

    function Get-IsShiftPressed {
        param([Parameter(Mandatory)]$KeyInfo)
        return (($KeyInfo.ControlKeyState -band $shiftMask) -ne 0)
    }

    function Normalize-EditorText {
        param([Parameter(Mandatory)][string]$Text)
        $t = $Text -replace "`r`n", "`n"
        $t = $t -replace "`r", "`n"
        return $t
    }

    function Insert-TextAtCursor {
        param([Parameter(Mandatory)][string]$InsertText)

        $t = Normalize-EditorText -Text $InsertText
        if ($null -eq $t) { return }
        if ($t.Length -eq 0) { return }

        $parts = $t.Split("`n")

        $current = $state.Lines[$state.CursorLine]
        if ($null -eq $current) { $current = "" }

        if ($parts.Length -le 1) {
            $before = if ($state.CursorCol -gt 0) { $current.Substring(0, $state.CursorCol) } else { "" }
            $after = if ($state.CursorCol -lt $current.Length) { $current.Substring($state.CursorCol) } else { "" }
            $state.Lines[$state.CursorLine] = $before + $parts[0] + $after
            $state.CursorCol = $state.CursorCol + $parts[0].Length
            return
        }

        $before = if ($state.CursorCol -gt 0) { $current.Substring(0, $state.CursorCol) } else { "" }
        $after = if ($state.CursorCol -lt $current.Length) { $current.Substring($state.CursorCol) } else { "" }

        $state.Lines[$state.CursorLine] = $before + $parts[0]
        for ($i = 1; $i -lt $parts.Length; $i++) {
            $insertLine = $parts[$i]
            if ($i -eq ($parts.Length - 1)) {
                $state.Lines.Insert($state.CursorLine + $i, $insertLine + $after)
            } else {
                $state.Lines.Insert($state.CursorLine + $i, $insertLine)
            }
        }

        $state.CursorLine = $state.CursorLine + ($parts.Length - 1)
        $state.CursorCol = $parts[$parts.Length - 1].Length

        if ($state.Lines.Count -gt 1) { $state.MultiLineEngaged = $true }
    }

    function New-LineAtCursor {
        $current = $state.Lines[$state.CursorLine]
        if ($null -eq $current) { $current = "" }

        $before = if ($state.CursorCol -gt 0) { $current.Substring(0, $state.CursorCol) } else { "" }
        $after = if ($state.CursorCol -lt $current.Length) { $current.Substring($state.CursorCol) } else { "" }

        $state.Lines[$state.CursorLine] = $before
        $state.Lines.Insert($state.CursorLine + 1, $after)

        $state.CursorLine = $state.CursorLine + 1
        $state.CursorCol = 0

        if ($state.Lines.Count -gt 1) { $state.MultiLineEngaged = $true }
    }

    function Backspace-AtCursor {
        if ($state.CursorCol -gt 0) {
            $current = $state.Lines[$state.CursorLine]
            if ($null -eq $current) { $current = "" }

            $before = $current.Substring(0, $state.CursorCol - 1)
            $after = if ($state.CursorCol -lt $current.Length) { $current.Substring($state.CursorCol) } else { "" }
            $state.Lines[$state.CursorLine] = $before + $after
            $state.CursorCol = $state.CursorCol - 1
            return
        }

        if ($state.CursorLine -gt 0) {
            $prev = $state.Lines[$state.CursorLine - 1]
            $cur = $state.Lines[$state.CursorLine]
            if ($null -eq $prev) { $prev = "" }
            if ($null -eq $cur) { $cur = "" }

            $newCol = $prev.Length
            $state.Lines[$state.CursorLine - 1] = $prev + $cur
            $state.Lines.RemoveAt($state.CursorLine)

            $state.CursorLine = $state.CursorLine - 1
            $state.CursorCol = $newCol
        }
    }

    function Delete-AtCursor {
        $current = $state.Lines[$state.CursorLine]
        if ($null -eq $current) { $current = "" }

        if ($state.CursorCol -lt $current.Length) {
            $before = if ($state.CursorCol -gt 0) { $current.Substring(0, $state.CursorCol) } else { "" }
            $after = if (($state.CursorCol + 1) -lt $current.Length) { $current.Substring($state.CursorCol + 1) } else { "" }
            $state.Lines[$state.CursorLine] = $before + $after
            return
        }

        if ($state.CursorLine -lt ($state.Lines.Count - 1)) {
            $next = $state.Lines[$state.CursorLine + 1]
            if ($null -eq $next) { $next = "" }
            $state.Lines[$state.CursorLine] = $current + $next
            $state.Lines.RemoveAt($state.CursorLine + 1)
        }
    }

    function Move-CursorLeft {
        if ($state.CursorCol -gt 0) {
            $state.CursorCol = $state.CursorCol - 1
            return
        }
        if ($state.CursorLine -gt 0) {
            $state.CursorLine = $state.CursorLine - 1
            $prev = $state.Lines[$state.CursorLine]
            if ($null -eq $prev) { $prev = "" }
            $state.CursorCol = $prev.Length
        }
    }

    function Move-CursorRight {
        $current = $state.Lines[$state.CursorLine]
        if ($null -eq $current) { $current = "" }

        if ($state.CursorCol -lt $current.Length) {
            $state.CursorCol = $state.CursorCol + 1
            return
        }
        if ($state.CursorLine -lt ($state.Lines.Count - 1)) {
            $state.CursorLine = $state.CursorLine + 1
            $state.CursorCol = 0
        }
    }

    function Move-CursorUp {
        if ($state.CursorLine -le 0) { return }
        $state.CursorLine = $state.CursorLine - 1
        $current = $state.Lines[$state.CursorLine]
        if ($null -eq $current) { $current = "" }
        if ($state.CursorCol -gt $current.Length) { $state.CursorCol = $current.Length }
    }

    function Move-CursorDown {
        if ($state.CursorLine -ge ($state.Lines.Count - 1)) { return }
        $state.CursorLine = $state.CursorLine + 1
        $current = $state.Lines[$state.CursorLine]
        if ($null -eq $current) { $current = "" }
        if ($state.CursorCol -gt $current.Length) { $state.CursorCol = $current.Length }
    }

    function Clear-InputBuffer {
        $state.Lines.Clear()
        [void]$state.Lines.Add("")
        $state.CursorLine = 0
        $state.CursorCol = 0
        $state.ViewTop = 0
        $state.MultiLineEngaged = $false
        $state.Focus = "Input"
        $state.ActionIndex = 2
        $state.HistoryPos = $state.HistoryEntries.Count
        $state.HistoryOriginal = ""
    }

    function Apply-HistoryUp {
        if ($state.HistoryEntries.Count -le 0) { return }
        if ($state.HistoryPos -eq $state.HistoryEntries.Count) { $state.HistoryOriginal = [string]$state.Lines[0] }
        if ($state.HistoryPos -gt 0) {
            $state.HistoryPos = $state.HistoryPos - 1
            $state.Lines[0] = [string]$state.HistoryEntries[$state.HistoryPos]
            $state.CursorLine = 0
            $state.CursorCol = $state.Lines[0].Length
        }
    }

    function Apply-HistoryDown {
        if ($state.HistoryEntries.Count -le 0) { return }
        if ($state.HistoryPos -lt $state.HistoryEntries.Count) {
            $state.HistoryPos = $state.HistoryPos + 1
            if ($state.HistoryPos -eq $state.HistoryEntries.Count) {
                $state.Lines[0] = [string]$state.HistoryOriginal
            } else {
                $state.Lines[0] = [string]$state.HistoryEntries[$state.HistoryPos]
            }
            $state.CursorLine = 0
            $state.CursorCol = $state.Lines[0].Length
        }
    }

    function Truncate-Pad {
        param(
            [AllowEmptyString()][string]$Text,
            [Parameter(Mandatory)][int]$Width
        )
        $t = $Text
        if ($null -eq $t) { $t = "" }
        if ($t.Length -gt $Width) { return $t.Substring(0, $Width) }
        return $t.PadRight($Width)
    }

    function New-Coord {
        param(
            [Parameter(Mandatory)][int]$X,
            [Parameter(Mandatory)][int]$Y
        )
        return (New-Object -TypeName System.Management.Automation.Host.Coordinates -ArgumentList $X, $Y)
    }

    function Clear-EditorSurface {
        $size = $rawUi.WindowSize
        $width = [Math]::Max(20, [int]$size.Width)
        $toClear = [Math]::Max([int]$state.RenderedLines, 0)

        for ($i = 0; $i -lt $toClear; $i++) {
            $rawUi.CursorPosition = New-Coord -X 0 -Y ([int]$state.Origin.Y + $i)
            [Console]::Write((" " * $width))
        }

        $rawUi.CursorPosition = $state.Origin
    }

    function Render-Editor {
        $size = $rawUi.WindowSize
        $width = [Math]::Max(20, [int]$size.Width)
        $winTop = $rawUi.WindowPosition.Y
        $winHeight = [int]$size.Height

        $available = ($winTop + $winHeight) - [int]$state.Origin.Y - [int]$state.FooterLines
        if ($available -lt 3) { $available = 3 }
        $viewHeight = $available

        if ($state.CursorLine -lt $state.ViewTop) { $state.ViewTop = $state.CursorLine }
        if ($state.CursorLine -ge ($state.ViewTop + $viewHeight)) { $state.ViewTop = $state.CursorLine - $viewHeight + 1 }
        if ($state.ViewTop -lt 0) { $state.ViewTop = 0 }

        $scrollLeft = 0
        $maxVisible = $width
        if ($state.CursorCol -ge $maxVisible) { $scrollLeft = $state.CursorCol - $maxVisible + 1 }
        if ($scrollLeft -lt 0) { $scrollLeft = 0 }

        $toClear = [Math]::Max([int]$state.RenderedLines, ($viewHeight + [int]$state.FooterLines))
        for ($i = 0; $i -lt $toClear; $i++) {
            $rawUi.CursorPosition = New-Coord -X 0 -Y ([int]$state.Origin.Y + $i)
            [Console]::Write((" " * $width))
        }

        for ($i = 0; $i -lt $viewHeight; $i++) {
            $idx = $state.ViewTop + $i
            $text = ""
            if ($idx -lt $state.Lines.Count) { $text = [string]$state.Lines[$idx] }
            if ($null -eq $text) { $text = "" }

            $text = $text -replace "`t", "    "

            $left = 0
            if ($idx -eq $state.CursorLine) { $left = $scrollLeft }

            if ($left -gt $text.Length) { $left = $text.Length }
            $segment = if ($text.Length -gt $left) { $text.Substring($left) } else { "" }
            if ($segment.Length -gt $width) { $segment = $segment.Substring(0, $width) }

            $rawUi.CursorPosition = New-Coord -X 0 -Y ([int]$state.Origin.Y + $i)
            [Console]::Write((Truncate-Pad -Text $segment -Width $width))
        }

        $hint = "Tab: actions | Ctrl+Enter: finish | F2: insert clipboard | Esc/Ctrl+C: cancel"
        $rawUi.CursorPosition = New-Coord -X 0 -Y ([int]$state.Origin.Y + $viewHeight)
        [Console]::Write((Truncate-Pad -Text $hint -Width $width))

        $actionsLine = ""
        if ($state.Focus -eq "Footer") {
            $a0 = if ($state.ActionIndex -eq 0) { "[Edit]" } else { "Edit" }
            $a1 = if ($state.ActionIndex -eq 1) { "[Clear input]" } else { "Clear input" }
            $a2 = if ($state.ActionIndex -eq 2) { "[Finish]" } else { "Finish" }
            $actionsLine = "Actions:  $a0  $a1  $a2"
        } else {
            $actionsLine = "Actions:  Edit  Clear input  Finish"
        }

        $rawUi.CursorPosition = New-Coord -X 0 -Y ([int]$state.Origin.Y + $viewHeight + 1)
        [Console]::Write((Truncate-Pad -Text $actionsLine -Width $width))

        if ($state.Focus -eq "Input") {
            $curY = [int]$state.Origin.Y + ($state.CursorLine - $state.ViewTop)
            if ($curY -lt [int]$state.Origin.Y) { $curY = [int]$state.Origin.Y }
            if ($curY -ge ([int]$state.Origin.Y + $viewHeight)) { $curY = [int]$state.Origin.Y + $viewHeight - 1 }

            $curX = $state.CursorCol - $scrollLeft
            if ($curX -lt 0) { $curX = 0 }
            if ($curX -ge $width) { $curX = $width - 1 }

            $rawUi.CursorPosition = New-Coord -X $curX -Y $curY
        } else {
            $rawUi.CursorPosition = New-Coord -X 0 -Y ([int]$state.Origin.Y + $viewHeight + 1)
        }

        $state.RenderedLines = $viewHeight + [int]$state.FooterLines
    }

    Render-Editor

    try {
        while ($true) {
            $key = $rawUi.ReadKey("NoEcho,IncludeKeyDown")
            if (-not $key.KeyDown) { continue }

            $vk = [int]$key.VirtualKeyCode
            $ctrl = Get-IsCtrlPressed -KeyInfo $key

            if ($vk -eq 27) {
                Clear-EditorSurface
                $rawUi.CursorPosition = $state.Origin
                throw [System.OperationCanceledException]::new("Selection cancelled.")
            }

            if ($ctrl -and ($vk -eq 13)) {
                break
            }

            if ($state.Focus -eq "Footer") {
                if ($vk -eq 9) {
                    $state.Focus = "Input"
                    Render-Editor
                    continue
                }

                if ($vk -eq 37) {
                    if ($state.ActionIndex -gt 0) { $state.ActionIndex = $state.ActionIndex - 1 } else { $state.ActionIndex = 2 }
                    Render-Editor
                    continue
                }

                if ($vk -eq 39) {
                    if ($state.ActionIndex -lt 2) { $state.ActionIndex = $state.ActionIndex + 1 } else { $state.ActionIndex = 0 }
                    Render-Editor
                    continue
                }

                if ($vk -eq 13) {
                    if ($state.ActionIndex -eq 0) {
                        $state.Focus = "Input"
                    } elseif ($state.ActionIndex -eq 1) {
                        Clear-InputBuffer
                    } elseif ($state.ActionIndex -eq 2) {
                        break
                    }
                    Render-Editor
                    continue
                }

                Render-Editor
                continue
            }

            # Input focus
            if ($vk -eq 9) {
                $state.Focus = "Footer"
                $state.ActionIndex = 2
                Render-Editor
                continue
            }

            if ($vk -eq 113) {
                $clip = Get-ClipboardTextRobust
                if (-not [string]::IsNullOrEmpty($clip)) {
                    Insert-TextAtCursor -InsertText $clip
                }
                Render-Editor
                continue
            }

            if ($vk -eq 13) {
                New-LineAtCursor
                Render-Editor
                continue
            }

            if ($vk -eq 8) {
                Backspace-AtCursor
                Render-Editor
                continue
            }

            if ($vk -eq 46) {
                Delete-AtCursor
                Render-Editor
                continue
            }

            if ($vk -eq 36) {
                $state.CursorCol = 0
                Render-Editor
                continue
            }

            if ($vk -eq 35) {
                $curLineText = $state.Lines[$state.CursorLine]
                if ($null -eq $curLineText) { $curLineText = "" }
                $state.CursorCol = $curLineText.Length
                Render-Editor
                continue
            }

            if ($vk -eq 37) {
                Move-CursorLeft
                Render-Editor
                continue
            }

            if ($vk -eq 39) {
                Move-CursorRight
                Render-Editor
                continue
            }

            if ($vk -eq 38) {
                if (-not $state.MultiLineEngaged -and $state.Lines.Count -eq 1) {
                    Apply-HistoryUp
                } else {
                    Move-CursorUp
                }
                Render-Editor
                continue
            }

            if ($vk -eq 40) {
                if (-not $state.MultiLineEngaged -and $state.Lines.Count -eq 1) {
                    Apply-HistoryDown
                } else {
                    Move-CursorDown
                }
                Render-Editor
                continue
            }

            $ch = $key.Character
            if ($ch -ne [char]0) {
                $code = [int][char]$ch
                if ($code -ge 32) {
                    Insert-TextAtCursor -InsertText ([string]$ch)
                    Render-Editor
                    continue
                }
            }

            Render-Editor
        }
    }
    finally {
        # Always clear the rendered editor surface before returning/throwing.
        Clear-EditorSurface
        $rawUi.CursorPosition = $state.Origin
    }

    $rawUi.CursorPosition = New-Coord -X 0 -Y ([int]$state.Origin.Y + [int]$state.RenderedLines)
    [Console]::WriteLine("")

    return ($state.Lines -join "`n")
}

function Parse-FileSelectionTextToTokens {
    param([Parameter(Mandatory)][string]$Text)

    $t = $Text -replace "`r`n", "`n"
    $t = $t -replace "`r", "`n"

    $tokens = New-Object System.Collections.Generic.List[string]

    function Add-Token {
        param([Parameter(Mandatory)][string]$Tok)

        $tok = $Tok
        if ($null -eq $tok) { return }
        $tok = $tok.Trim()
        if ([string]::IsNullOrWhiteSpace($tok)) { return }

        $tok = [regex]::Replace($tok, '^[`"''“”‘’]+', '')
        $tok = [regex]::Replace($tok, '[`"''“”‘’]+$', '')
        $tok = $tok.Trim()
        if ([string]::IsNullOrWhiteSpace($tok)) { return }

        $tok = $tok.TrimEnd('.',';',',',')',']','}')
        $tok = $tok.TrimStart('(','[','{')
        $tok = $tok.Trim()
        if ([string]::IsNullOrWhiteSpace($tok)) { return }

        $tok = $tok -replace '\\', '/'
        while ($tok.StartsWith("./")) { $tok = $tok.Substring(2) }
        $tok = $tok.TrimStart('/')

        # Strip line-range suffixes like ":1-10" or ":1-(10)" (avoid breaking Windows drive letters)
        if (-not ($tok -match '^[A-Za-z]:/')) {
            $colonIndex = $tok.IndexOf(':')
            if ($colonIndex -ge 0) {
                $tail = $tok.Substring($colonIndex)
                if ($tail -match '^:\d') {
                    $tok = $tok.Substring(0, $colonIndex).Trim()
                }
            }
        }

        if ([string]::IsNullOrWhiteSpace($tok)) { return }
        $tokens.Add($tok)
    }

    # Pass 1: backtick spans
    foreach ($m in [regex]::Matches($t, '`([^`]+)`')) {
        $val = $m.Groups[1].Value
        if (-not [string]::IsNullOrWhiteSpace($val)) { Add-Token -Tok $val }
    }

    # Pass 2: path-like patterns with directory separators + extension
    foreach ($m in [regex]::Matches($t, '(?i)(?:[A-Za-z0-9_.-]+[\\/])+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}')) {
        $val = $m.Value
        if (-not [string]::IsNullOrWhiteSpace($val)) { Add-Token -Tok $val }
    }

    # Pass 3: filename-with-extension tokens
    foreach ($m in [regex]::Matches($t, '(?i)\b[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}\b')) {
        $val = $m.Value
        if (-not [string]::IsNullOrWhiteSpace($val)) { Add-Token -Tok $val }
    }

    # Pass 4: standalone directory-like lines (after stripping bullets/numbering)
    $lines = $t.Split("`n")
    foreach ($raw in $lines) {
        $line = ($raw -as [string])
        if ($null -eq $line) { continue }

        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }

        if ($line -match '^\s*```') { continue }

        $line = $line -replace '^\s*>\s*', ''
        $line = $line -replace '^\s*(?:\d+[\.\)]\s+)', ''
        $line = $line -replace '^\s*(?:[-*+•]\s+)', ''
        $line = $line -replace '^\s*\[\s*[xX]\s*\]\s*', ''
        $line = $line -replace '^\s*\[\s*\]\s*', ''
        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }

        $line = [regex]::Replace($line, '^[`"''“”‘’]+', '')
        $line = [regex]::Replace($line, '[`"''“”‘’]+$', '')
        $line = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { continue }

        if ($line -match '^[A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)*[\\/]?$') {
            Add-Token -Tok $line
        }
    }

    # De-duplicate case-insensitively, preserving first occurrence order
    $set = New-Object System.Collections.Generic.HashSet[string] ([System.StringComparer]::OrdinalIgnoreCase)
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($tok in $tokens) {
        if ([string]::IsNullOrWhiteSpace($tok)) { continue }
        if ($set.Add($tok)) { $out.Add($tok) }
    }

    return ,$out.ToArray()
}

function Resolve-FileSelectionAgainstProject {
    param(
        [Parameter(Mandatory)][object[]]$Files,
        [Parameter(Mandatory)][string[]]$SelectionTokens,
        [Parameter(Mandatory)][string]$ProjectRootPath
    )

    $rootAbs = (Resolve-Path $ProjectRootPath).Path.TrimEnd('\','/')

    # Defensive: flatten list-like containers that may have been bound as a single element
    $candidateFiles = New-Object System.Collections.Generic.List[object]
    foreach ($x in $Files) {
        if ($null -eq $x) { continue }

        $hasRel = ($null -ne $x.PSObject.Properties['RelativePath'])
        $isDict = ($x -is [System.Collections.IDictionary])
        $isListLike = ($x -is [System.Collections.IList])

        if (-not $hasRel -and $isListLike -and -not $isDict -and -not ($x -is [string])) {
            foreach ($y in $x) {
                if ($null -ne $y) { $candidateFiles.Add($y) }
            }
        } else {
            $candidateFiles.Add($x)
        }
    }

    $selected = New-Object System.Collections.Generic.List[object]
    $seen = New-Object System.Collections.Generic.HashSet[string] ([System.StringComparer]::OrdinalIgnoreCase)

    foreach ($rawTok in $SelectionTokens) {
        if ([string]::IsNullOrWhiteSpace($rawTok)) { continue }

        $tok = ($rawTok -as [string])
        if ($null -eq $tok) { continue }

        $tok = $tok.Trim()
        if ([string]::IsNullOrWhiteSpace($tok)) { continue }

        $tok = $tok -replace '\\', '/'
        while ($tok.StartsWith("./")) { $tok = $tok.Substring(2) }
        $tok = $tok.Trim()

        # If an absolute path is provided and is under the project root, convert to a relative token
        $maybeAbs = $false
        if ($tok -match '^[A-Za-z]:/' -or $tok.StartsWith("//")) { $maybeAbs = $true }

        if ($maybeAbs) {
            try {
                $tokCandidate = $tok -replace '/', '\'
                $abs = (Resolve-Path -LiteralPath $tokCandidate -ErrorAction Stop).Path
                $absNorm = $abs.TrimEnd('\','/')
                if ($absNorm.StartsWith($rootAbs, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $rel = $absNorm.Substring($rootAbs.Length).TrimStart('\','/')
                    $tok = Normalize-RelPath $rel
                }
            } catch {
                # Keep token as-is
            }
        } else {
            $tok = Normalize-RelPath $tok
        }

        if ([string]::IsNullOrWhiteSpace($tok)) { continue }

        $explicitDirToken = $tok.EndsWith('/')
        if ($explicitDirToken) { $tok = $tok.TrimEnd('/') }

        $hasSlash = $tok.Contains('/')
        $hasWildcards = ($tok -match '[\*\?]')

        $looksLikeExtension = ($tok -match '\.[A-Za-z0-9]{1,10}$')
        $treatAsDirByPrefix = $false

        if (-not $explicitDirToken -and -not $hasWildcards -and -not $looksLikeExtension) {
            $prefixProbe = $tok + "/"
            foreach ($f in $candidateFiles) {
                if ($null -eq $f) { continue }
                if ($null -eq $f.PSObject.Properties['RelativePath']) { continue }
                $rpProbe = Normalize-RelPath ([string]$f.RelativePath)
                if ($rpProbe.StartsWith($prefixProbe, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $treatAsDirByPrefix = $true
                    break
                }
            }
        }

        $isDirToken = $explicitDirToken -or $treatAsDirByPrefix

        $fileMatches = New-Object System.Collections.Generic.List[object]

        foreach ($f in $candidateFiles) {
            if ($null -eq $f) { continue }
            if ($null -eq $f.PSObject.Properties['RelativePath']) { continue }

            $rp = Normalize-RelPath ([string]$f.RelativePath)
            $fileName = [System.IO.Path]::GetFileName($rp)
            $fileStem = [System.IO.Path]::GetFileNameWithoutExtension($rp)

            $isMatch = $false

            if ($isDirToken) {
                $prefix = $tok + "/"
                if ($rp.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { $isMatch = $true }
            } elseif ($hasSlash) {
                if ($hasWildcards) {
                    if ($rp -like $tok -or $rp -like "*/$tok") { $isMatch = $true }
                } else {
                    if ($rp.Equals($tok, [System.StringComparison]::OrdinalIgnoreCase)) { $isMatch = $true }
                    elseif ($rp.EndsWith("/$tok", [System.StringComparison]::OrdinalIgnoreCase)) { $isMatch = $true }
                }
            } else {
                if ($hasWildcards) {
                    if ($fileName -like $tok -or $fileStem -like $tok) { $isMatch = $true }
                } else {
                    if ($fileName.Equals($tok, [System.StringComparison]::OrdinalIgnoreCase)) { $isMatch = $true }
                    elseif (($tok -notmatch '\.') -and $fileStem.Equals($tok, [System.StringComparison]::OrdinalIgnoreCase)) { $isMatch = $true }
                }
            }

            if ($isMatch) { $fileMatches.Add($f) }
        }

        foreach ($m in $fileMatches) {
            $key = Normalize-RelPath ([string]$m.RelativePath)
            if ($seen.Add($key)) { $selected.Add($m) }
        }
    }

    return @{
        Selected = ($selected | Sort-Object RelativePath)
    }
}

# Main
Write-Host "Generating file listing..." -ForegroundColor Green

$resolvedProjectPath = Resolve-Path $ProjectPath
$projectRootPath = $resolvedProjectPath.Path
Write-Host "Project path: $projectRootPath" -ForegroundColor Yellow

$outputFileTemplate = Add-ProjectNameToOutputPath -Path $OutputFile -ProjectRootPath $projectRootPath
$OutputFile = Get-TimestampedOutputPath -Path $outputFileTemplate

$repoRoot = Get-RepoRootIfAvailable -Path $projectRootPath

$gitIgnoreLines = @(Get-GitIgnorePatterns -ProjectPath $projectRootPath)

if ($gitIgnoreLines.Count -eq 1 -and [string]::IsNullOrWhiteSpace([string]$gitIgnoreLines[0])) {
    $gitIgnoreLines = @()
}

$rules = if ($gitIgnoreLines.Count -gt 0) {
    @(Compile-GitIgnoreRules -GitIgnoreLines $gitIgnoreLines)
} else {
    @()
}

$filesToInclude = @()
if ($repoRoot) {
    Write-Host "Git worktree detected. Using Git discovery, then applying .gitignore export filtering." -ForegroundColor Yellow
    $filesToInclude = Get-GitTrackedAndUntrackedNotIgnored -RepoRoot $repoRoot -ResolvedProjectPath $projectRootPath
} else {
    Write-Host "Git not available or not a worktree. Falling back to best-effort .gitignore parsing." -ForegroundColor Yellow
    $filesToInclude = Get-AllProjectFilesFallback -ProjectPath $projectRootPath -Rules $rules
}

# Apply .gitignore-style export filtering to all discovered files, including tracked files.
$filesToInclude = $filesToInclude | Where-Object {
    -not (Test-IsIgnoredByRules -RelativePath $_.RelativePath -Rules $rules)
}

# Filter out generated/platform files (Android, APKs, temp files)
$filesToInclude = $filesToInclude | Where-Object {
    $r = $_.RelativePath -replace '\\', '/'
    -not ($r -match '^android/' -or $r -match '^dist_apk/' -or $r -eq '.adb-wifi-connect-port.txt')
}

$selectionMode = $SelectFiles -or (-not [string]::IsNullOrWhiteSpace($SelectFilesText))
$selectedFilePathsForFinalOutput = @()

if ($selectionMode) {
    Write-Host "File selection mode enabled. Paste content, then finish with Ctrl+Enter or footer action." -ForegroundColor Yellow
    Write-Host "Tab toggles footer actions. Enter inserts a newline. Esc cancels selection mode." -ForegroundColor Yellow
    Write-Host ""

    $rawSelectionText = $SelectFilesText

    if ([string]::IsNullOrWhiteSpace($rawSelectionText) -and $SelectFiles) {
        try {
            $rawSelectionText = Read-FileSelectionTextInteractive
        } catch [System.OperationCanceledException] {
            Write-Host "Selection cancelled." -ForegroundColor Yellow
            exit 1
        }
    }

    $tokens = @()
    if (-not [string]::IsNullOrWhiteSpace($rawSelectionText)) {
        $tokens = Parse-FileSelectionTextToTokens -Text $rawSelectionText
    }

    if (-not $tokens -or $tokens.Count -eq 0) {
        $filesToInclude = @()
    } else {
        # Defensive: materialize as a plain object[] of file items (avoids binding a List as a single element)
        $filesForResolution = @()
        foreach ($fi in $filesToInclude) { $filesForResolution += ,$fi }

        $resolved = Resolve-FileSelectionAgainstProject -Files $filesForResolution -SelectionTokens $tokens -ProjectRootPath $projectRootPath
        $filesToInclude = $resolved.Selected
    }

    if ($filesToInclude.Count -gt 0) {
        $selectedFilePathsForFinalOutput = @(
            $filesToInclude | ForEach-Object {
                Normalize-RelPath $_.RelativePath
            }
        )
    } else {
        $selectedFilePathsForFinalOutput = @()
    }
}

# Intentionally not printed; final output summary is emitted after the file is generated.

$enrichedFiles = New-Object System.Collections.Generic.List[object]
foreach ($f in $filesToInclude) {
    $info = Get-FileTextAndLineCount -FilePath $f.FullPath
    $enrichedFiles.Add([pscustomobject]@{
        FullPath         = $f.FullPath
        RelativePath     = $f.RelativePath
        LineCount        = $info.LineCount
        Content          = $info.Content
        ListingStartLine = 0
        ListingEndLine   = 0
    })
}
$filesToInclude = $enrichedFiles

# Calculate where each file block will appear in the generated listing document.
# The overview is rendered before the file-content section, so its line count must be known first.
$preRangeOverviewLineCount = 4

if (-not $selectionMode) {
    $preRangeOverviewLines = Get-ProjectOverviewLines -Files $filesToInclude -ExpandFolders $ExpandOverviewFolders
    $preRangeOverviewLineCount += 1 # "# Project structure:"
    $preRangeOverviewLineCount += $preRangeOverviewLines.Count
    $preRangeOverviewLineCount += 2 # blank lines after project structure
}

$preRangeOverviewLineCount += 2 # "# Project Files Listing" + blank line

$firstFileStartLine = $preRangeOverviewLineCount + 1
Set-GeneratedListingLineRanges -Files $filesToInclude -FirstFileStartLine $firstFileStartLine

$outputContent = New-Object System.Text.StringBuilder

# Overview section (expanded structure for selected folders)
[void]$outputContent.AppendLine("# Project Overview (included; respecting .gitignore)")
[void]$outputContent.AppendLine("# Generated on: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
[void]$outputContent.AppendLine("# Total included files: $($filesToInclude.Count)")
[void]$outputContent.AppendLine("")

if (-not $selectionMode) {
    [void]$outputContent.AppendLine("# Project structure:")
    $overviewLines = Get-ProjectOverviewLines -Files $filesToInclude -ExpandFolders $ExpandOverviewFolders
    foreach ($l in $overviewLines) {
        [void]$outputContent.AppendLine($l)
    }
    [void]$outputContent.AppendLine("")
    [void]$outputContent.AppendLine("")
}

# Full per-file content section
[void]$outputContent.AppendLine("# Project Files Listing")
[void]$outputContent.AppendLine("")

foreach ($f in $filesToInclude) {
    $rel = Normalize-RelPath $f.RelativePath
    $lineCount = $f.LineCount
    $content = $f.Content

    [void]$outputContent.AppendLine("$rel`:1-($lineCount)")
    [void]$outputContent.AppendLine('```')

    $numberedContent = Convert-ContentToNumberedLines -Content $content -LineCount $lineCount

    if (-not [string]::IsNullOrEmpty($numberedContent)) {
        [void]$outputContent.Append($numberedContent)
    } else {
        [void]$outputContent.AppendLine("")
    }

    [void]$outputContent.AppendLine('```')
    [void]$outputContent.AppendLine("")
}

$outputPath = Join-Path $projectRootPath $OutputFile
$outputContent.ToString() | Out-File -LiteralPath $outputPath -Encoding UTF8

if ($CleanPreviousListings) {
    [void](Remove-PreviousListingSnapshots -ProjectRootPath $projectRootPath -OutputFileTemplate $outputFileTemplate -KeepFilePath $outputPath)
}

if ($selectionMode) {
    Write-Host "File listing generated: `"$outputPath`" containing the following selected files ($($filesToInclude.Count)):" -ForegroundColor Green

    foreach ($relPath in $selectedFilePathsForFinalOutput) {
        Write-Host ("- " + $relPath) -ForegroundColor Green
    }
} else {
    Write-Host "File listing generated: `"$outputPath`" containing $($filesToInclude.Count) files." -ForegroundColor Green
}

# Clipboard copying is temporarily disabled.
# if ($CopyToClipboard) {
#     [void](TryCopyFileToClipboard -FilePath $outputPath)
# }
