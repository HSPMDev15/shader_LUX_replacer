import vs
import os
import re
from PySide import QtGui
from PySide import QtCore

def skipDialog():
    """change the return to false if you want to see the dialog box"""
    return False

# Handles both quoted and unquoted forms (and just JUST for some cases the commented lines)
_SHADER_RE = re.compile(r'^\s*"?([A-Za-z_][A-Za-z0-9_]*)"?\s*(?://.*)?$')

def _detectShader(content):
    """Return (shaderName, lineIndex) from the first non empty, non comment line"""
    for idx, line in enumerate(content.splitlines(True)):
        stripped = line.strip()
        if not stripped or stripped.startswith('//'):
            continue
        m = _SHADER_RE.match(stripped)
        if m:
            return m.group(1), idx
        break
    return None, None


def _replaceShaderLine(content, lineIndex, newShader):
    lines = content.splitlines(True)
    originalLine = lines[lineIndex]
    lineEnd = ''
    if originalLine.endswith('\r\n'):
        lineEnd = '\r\n'
    elif originalLine.endswith('\n'):
        lineEnd = '\n'
    leadingWhitespace = originalLine[:len(originalLine) - len(originalLine.lstrip())]
    stripped = originalLine.strip()

    comment = ''
    comment_match = re.search(r'(?<!:)//.*', stripped)
    if comment_match:
        comment  = comment_match.group(0)
        stripped = stripped[:comment_match.start()].strip()

    if stripped.startswith('"') and stripped.endswith('"'):
        newStripped = '"' + newShader + '"'
    else:
        newStripped = newShader

    lines[lineIndex] = leadingWhitespace + newStripped + comment + lineEnd
    return ''.join(lines)

def _getSearchPaths():
    paths = []
    try:
        gameInfoFull = vs.g_pFullFileSystem.RelativePathToFullPath('gameinfo.txt', 'GAME')
        if not gameInfoFull or not os.path.isfile(gameInfoFull):
            return paths

        gameInfoFull = gameInfoFull.replace('\\', '/')
        gameInfoDir  = os.path.dirname(gameInfoFull)           # .../game/usermod
        engineRoot   = os.path.dirname(gameInfoDir)            # .../game

        with open(gameInfoFull, 'r') as f:
            content = f.read()

        sp_match = re.search(
            r'SearchPaths\s*\{([^}]*)\}', content, re.IGNORECASE | re.DOTALL
        )
        if not sp_match:
            # just return gameInfoDir and engineRoot subdirs
            paths.append(gameInfoDir)
            paths.append(engineRoot)
            return paths

        sp_block = sp_match.group(1)

        # gameinfo is writted on Keyvalues (it could be possibly quoted and possibly with //)
        line_re = re.compile( r'^\s*(?:"[^"]+"|\S+)\s+(?:"([^"]+)"|(\S+))',re.MULTILINE)

        for m in line_re.finditer(sp_block):
            raw = (m.group(1) or m.group(2) or '').strip()

            # Skip VPKs, wildcards, comments
            if not raw or raw.startswith('//'):
                continue
            if raw.endswith('.vpk') or raw.endswith('/*') or raw == '.':
                continue

            raw = raw.replace('\\', '/')
            raw = re.sub(r'\|gameinfo_path\|', gameInfoDir + '/', raw, flags=re.IGNORECASE)
            raw = re.sub(r'\|all_source_engine_paths\|', engineRoot + '/', raw, flags=re.IGNORECASE)

            # Absolute path already?
            if os.path.isabs(raw):
                candidate = os.path.normpath(raw).replace('\\', '/')
            else:
                candidate = os.path.normpath(os.path.join(engineRoot, raw)).replace('\\', '/')

            if os.path.isdir(candidate) and candidate not in paths:
                paths.append(candidate)

    except Exception as e:
        sfm.console('echo [LUX_SHADER_REPLACER] _getSearchPaths failed: ' + str(e))

    return paths


# Cache so we only parse gameinfo.txt once per script run
_SEARCH_PATHS_CACHE = []
_SEARCH_PATHS_READY = [False]


def _resolveVmtPath(relPath):
    """Resolve a material relative path to an absolute .vmt on disk"""

    relPath = relPath.replace('\\', '/').strip('/')

    matRel = 'materials/' + relPath + '.vmt'

    # Source filesystem first
    try:
        full = vs.g_pFullFileSystem.RelativePathToFullPath(matRel,  'GAME')

        if full:

            full = os.path.normpath(full).replace('\\', '/')

            if os.path.isfile(full):
                return full

    except Exception as e:
        sfm.console('echo [LUX_SHADER_REPLACER] filesystem resolve failed: ' + str(e))

    if not _SEARCH_PATHS_READY[0]:

        _SEARCH_PATHS_CACHE.extend(_getSearchPaths())
        _SEARCH_PATHS_READY[0] = True

    for sp in _SEARCH_PATHS_CACHE:

        candidate = os.path.normpath(os.path.join(sp, matRel)).replace('\\', '/')

        if os.path.isfile(candidate):
            return candidate

    return None

def _buildCombinedPath(matBase, cdTexturePath):

    matBase = str(matBase).replace('\\', '/').strip()
    cdTexturePath = str(cdTexturePath).replace('\\', '/').strip()

    matBase = re.sub(r'/+', '/', matBase)
    cdTexturePath = re.sub(r'/+', '/', cdTexturePath)

    matBase = matBase.strip('/')
    cdTexturePath = cdTexturePath.strip('/')

    candidates = []

    if '/' in matBase:

        candidates.append(matBase)

        basename = matBase.split('/')[-1]

        if basename != matBase:
            candidates.append(basename)

        return candidates

    if cdTexturePath:
        candidates.append(cdTexturePath + '/' + matBase)

    candidates.append(matBase)

    return candidates

def _collectVmts():
    """Return (modelPath, [(relPath, absPath), ...]) for the current animation set"""

    animSet   = sfm.GetCurrentAnimationSet()
    gameModel = animSet.gameModel

    modelPath = ''

    try:
        modelPath = str(gameModel.modelName)
    except Exception:
        pass

    try:
        sHDR = gameModel.GetStudioHdr()

        seenResolved = set()
        seenAttempted = set()

        paths = []

        for i in range(sHDR.numtextures):
            matBase = str( sHDR.pTexture(i).pszName() )

            for j in range(sHDR.numcdtextures):

                cdPath = str(sHDR.pCdtexture(j))

                combinedCandidates = _buildCombinedPath(matBase, cdPath)

                resolvedEntry = None

                for combined in combinedCandidates:

                    combined = combined.replace('\\', '/')
                    combined = combined.lower()

                    if combined in seenAttempted:
                        continue

                    seenAttempted.add(combined)

                    resolved = _resolveVmtPath(combined)

                    if resolved:

                        resolvedKey = resolved.lower()

                        if resolvedKey not in seenResolved:

                            seenResolved.add(resolvedKey)

                            resolvedEntry = (combined,resolved)

                        break

                if resolvedEntry:
                    paths.append(resolvedEntry)

        return modelPath, paths

    except Exception as e:
        sfm.console('echo [LUX_SHADER_REPLACER] StudioHdr failed, falling back to materials node: ' + str(e))

    vmtList = []

    try:
        for i in range(gameModel.materials.Count()):
            mat = gameModel.materials.Get(i)

            try:
                mtlName = mat.GetValue(  'mtlName' )

                if not mtlName:
                    continue

                mtlName = mtlName.replace('\\', '/')
                mtlName = os.path.normpath(mtlName)
                mtlName = mtlName.lower()

                resolved = _resolveVmtPath(mtlName)

                if resolved:
                    vmtList.append( ( mtlName, resolved ) )

            except Exception:
                pass

    except Exception as e:
        sfm.console('echo [LUX_SHADER_REPLACER] Materials node fallback also failed: ' + str(e))

    return modelPath, vmtList


def _addTF2Compatibility(content):

    if re.search(r'\$TF2Compatibility', content, re.IGNORECASE):
        return content

    pos = content.rfind('}')

    if pos == -1:
        return content

    return (content[:pos] + '\n\t"$TF2Compatibility" "1"\n' +content[pos:])


def _reloadMaterials():
    try:
        from vs import g_pMaterialSystem
        g_pMaterialSystem.UncacheAllMaterials()
    except Exception:
        sfm.console('echo [LUX_SHADER_REPLACER] Unable to refresh materials, run in console: `mat_reloadallmaterials')


def _applyChanges(changeList, addTF2Compat=False):
    """
    changeList: [(absPath, lineIndex, newShader), ...]
    Returns (successCount, errorList)
    """
    success = 0
    errors  = []
    for absPath, lineIndex, newShader in changeList:
        try:
            with open(absPath, 'r') as f:
                content = f.read()
            newContent = _replaceShaderLine(content, lineIndex, newShader)

            if addTF2Compat:
                newContent = _addTF2Compatibility(newContent)
                
            if newContent == content:
                continue
            with open(absPath, 'w') as f:
                f.write(newContent)
            success += 1
        except Exception as e:
            errors.append(os.path.basename(absPath) + ': ' + str(e))
    return success, errors

def _undoChanges(targets): # useful for retoids :P - turntwister 25/6/26
    success = 0
    errors = []

    tf2Pattern = re.compile(r'^\s*"?\$TF2Compatibility"?\s*"?(?:1)"?\s*\r?\n?', re.IGNORECASE | re.MULTILINE)

    for relPath, absPath in targets:
        if absPath is None:
            continue

        try:
            with open(absPath, 'r') as f:
                content = f.read()

            newContent = content

            shaderName, lineIndex = _detectShader(newContent)

            if shaderName and shaderName.upper().startswith('LUX_'):
                originalShader = shaderName[4:]
                newContent = _replaceShaderLine(newContent, lineIndex, originalShader)

            newContent = tf2Pattern.sub('', newContent)

            if newContent != content:
                with open(absPath, 'w') as f:
                    f.write(newContent)
                success += 1

        except Exception as e:
            errors.append('%s (%s)' % (relPath, str(e)))

    _reloadMaterials()

    return success, errors


def _showResult(success, skipped, unresolved, errors):
    summary  = 'Replaced shader in %d file(s).' % success
    summary += '\nSkipped (already LUX_): %d' % skipped
    if unresolved:
        summary += '\nNot found on disk: %d' % unresolved
    if errors:
        summary += '\n\nErrors:\n' + '\n'.join(errors[:10])
        QtGui.QMessageBox.warning(None, 'Done with errors', summary)
    else:
        QtGui.QMessageBox.information(None, 'Done', summary)


def _runSilent(vmtList): # average user never ends here since the dialog shows by default so i dont update it
    """Prepend LUX_ to every shader header without showing any dialog"""
    changeList = []
    skipped    = 0
    unresolved = 0

    for relPath, absPath in vmtList:
        if absPath is None:
            unresolved += 1
            continue
        try:
            with open(absPath, 'r') as f:
                content = f.read()
        except Exception:
            unresolved += 1
            continue

        shader, lineIdx = _detectShader(content)
        if shader is None:
            unresolved += 1
            continue
        if shader.upper().startswith('LUX_'):
            skipped += 1
            continue

        changeList.append((absPath, lineIdx, 'LUX_' + shader))

    success, errors = _applyChanges(changeList)
    _reloadMaterials()
    _showResult(success, skipped, unresolved, errors)


def _runDialog(modelPath, vmtList):
    """Show VMT selection dialog. User picks which VMTs to apply LUX_ to"""

    # Build entry data (contains vmtBaseName, absPath, lineIdx, currentShader)
    entries = []
    for relPath, absPath in vmtList:
        if absPath is None:
            entries.append((os.path.basename(relPath), None, None, None))
            continue
        try:
            with open(absPath, 'r') as f:
                content = f.read()
        except Exception:
            entries.append((os.path.basename(relPath), None, None, None))
            continue
        shader, lineIdx = _detectShader(content)
        entries.append((os.path.basename(relPath), absPath, lineIdx, shader))

    dlg = QtGui.QDialog()
    dlg.setWindowTitle('VMT Shader LUX Replace')
    dlg.setMinimumWidth(420)

    layout = QtGui.QVBoxLayout()
    layout.setSpacing(6)

    modelEdit = QtGui.QLineEdit(str(modelPath) if modelPath else '(unknown)')
    modelEdit.setReadOnly(True)
    layout.addWidget(modelEdit)

    foundOnDisk = sum(1 for _, ab, __, ___ in entries if ab is not None)
    layout.addWidget(QtGui.QLabel(
        'VMTs found: <b>%d</b>' % (len(vmtList))
    ))
    layout.addWidget(QtGui.QLabel(
        'Resolved on disk: <b>%d</b>' % (foundOnDisk)
    ))

    luxAllCheck = QtGui.QCheckBox('Apply LUX_ to all')
    luxAllCheck.setChecked(False)
    layout.addWidget(luxAllCheck)
    
    tf2CompatCheck = QtGui.QCheckBox('Add and Enable $TF2Compatibility')
    layout.addWidget(tf2CompatCheck)
    
    infoLabel = QtGui.QLabel(
    'NOTE: Current LUX shaders fully supported:<br>'
    '<b>VertexLitGeneric</b> - <b>UnLitGeneric</b>')
    
    infoLabel.setWordWrap(False)
    layout.addWidget(infoLabel)

    # List widget hidden when luxAllCheck is checked
    vmtList_widget = QtGui.QListWidget()
    vmtList_widget.setSelectionMode(QtGui.QAbstractItemView.ExtendedSelection)
    for name, absPath, lineIdx, shader in entries:
        label = name
        if shader is not None:
            label = '%s  [%s]' % (name, shader)
        elif absPath is None:
            label = '%s  (not found)' % name
        item = QtGui.QListWidgetItem(label)
        vmtList_widget.addItem(item)
    vmtList_widget.setVisible(True)
    layout.addWidget(vmtList_widget)

    def onCheckToggled(state):
        vmtList_widget.setVisible(not luxAllCheck.isChecked())
        dlg.adjustSize()

    luxAllCheck.stateChanged.connect(onCheckToggled)

    btnLayout = QtGui.QHBoxLayout()
    restoreBtn = QtGui.QPushButton('Restore')
    applyBtn  = QtGui.QPushButton('Apply')
    cancelBtn = QtGui.QPushButton('Cancel')

    btnLayout.addWidget(restoreBtn)
    btnLayout.addStretch()
    btnLayout.addWidget(applyBtn)
    btnLayout.addWidget(cancelBtn)

    layout.addLayout(btnLayout)

    def onRestore():
        if luxAllCheck.isChecked():
            targets = vmtList
        else:
            selected = set(vmtList_widget.row(i) for i in vmtList_widget.selectedItems())
            if not selected:
                QtGui.QMessageBox.warning(dlg, 'No selection', 'Select at least one VMT to restore')
                return
            targets = [vmtList[idx] for idx in selected]

        success, errors = _undoChanges(targets)
        
        if success > 0:
            sfm.console('echo [LUX_SHADER_REPLACER] Shader restore completed successfully')
        else:
            sfm.console('echo [LUX_SHADER_REPLACER] No files needed restoration')

        # anything could happen so better print if there is the rare case this encounter any errors
        if errors:
            sfm.console('echo [LUX_SHADER_REPLACER] Restore encountered errors in some files:')
            for err in errors[:10]:
                sfm.console('echo [LUX_SHADER_REPLACER]   %s' % err)
                
        dlg.accept()

    def onApply():
        if luxAllCheck.isChecked():
            targets = [(absPath, lineIdx, shader)
                       for _, absPath, lineIdx, shader in entries
                       if absPath is not None and lineIdx is not None
                       and shader is not None and not shader.upper().startswith('LUX_')]
        else:
            selected = set(vmtList_widget.row(i) for i in vmtList_widget.selectedItems())
            if not selected:
                QtGui.QMessageBox.warning(dlg, 'No selection', 'Select at least one VMT')
                return
            targets = []
            for idx in selected:
                _, absPath, lineIdx, shader = entries[idx]
                if absPath is None or lineIdx is None or shader is None:
                    continue
                if shader.upper().startswith('LUX_'):
                    continue
                targets.append((absPath, lineIdx, shader))

        if not targets:
            QtGui.QMessageBox.information(dlg, 'Nothing to do',
                'All selected VMTs already use LUX_ or could not be resolved')
            return

        changeList = [(absPath, lineIdx, 'LUX_' + shader)
                      for absPath, lineIdx, shader in targets]

        skipped    = sum(1 for _, __, ___, shader in entries
                         if shader is not None and shader.upper().startswith('LUX_'))
        unresolved = sum(1 for _, absPath, __, ___ in entries if absPath is None)

        success, errors = _applyChanges(changeList, tf2CompatCheck.isChecked())
        _reloadMaterials()
        _showResult(success, skipped, unresolved, errors)
        
        if not errors:
            sfm.console('echo [LUX] Shader replacement completed successfully')
            
        dlg.accept()

    restoreBtn.clicked.connect(onRestore)
    applyBtn.clicked.connect(onApply)
    cancelBtn.clicked.connect(dlg.reject)
    applyBtn.setDefault(True)

    dlg.setLayout(layout)
    dlg.setModal(True)
    dlg.exec_()


def _run():
    sfm.console('echo [LUX_SHADER_REPLACER] Hello there')

    try:
        modelPath, vmtList = _collectVmts()
    except Exception as e:
        QtGui.QMessageBox.critical(None, 'Error', 'Failed to collect VMTs:\n' + str(e))
        return

    if not vmtList:
        QtGui.QMessageBox.warning(None, 'No VMTs', 'No VMT paths found for this model')
        return

    if skipDialog():
        _runSilent(vmtList)
    else:
        _runDialog(modelPath, vmtList)

_run()
