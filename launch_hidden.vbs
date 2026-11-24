On Error Resume Next

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
batchFile = fso.BuildPath(folder, "start.bat")

' Always run the batch worker hidden. Forward only the supported auto-start flag.
extraArgs = ""
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "--start-hidden" Then
        extraArgs = " --start-hidden"
    End If
End If

' Use CALL so cmd.exe can parse a quoted path (including spaces) and its arguments.
command = "cmd.exe /d /c call " & Chr(34) & batchFile & Chr(34) & " --worker" & extraArgs
shell.Run command, 0, False

If Err.Number <> 0 Then
    MsgBox "Could not start YouTube Music Discord RPC." & vbCrLf & Err.Description, vbExclamation, "YTM Discord RPC"
End If
