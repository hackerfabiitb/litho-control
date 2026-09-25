$log = "C:\hdmi-log.csv"
$q = "SELECT * FROM __InstanceOperationEvent WITHIN 2 " +
"WHERE TargetInstance ISA 'Win32_PnPEntity' " +
"AND TargetInstance.PNPClass = 'Monitor'"

Register-CimIndicationEvent -Query $q -SourceIdentifier MonWatch -Action {
    $e = $Event.SourceEventArgs.NewEvent
    $act = switch ($e.CimClass.CimClassName) {
        '__InstanceCreationEvent' { 'PLUGGED' }
        '__InstanceDeletionEvent' { 'UNPLUGGED' }
        default { 'CHANGED' }
    }
    [pscustomobject]@{
        Time   = Get-Date -Format s
        Action = $act
        Device = $e.TargetInstance.Name
        Id     = $e.TargetInstance.PNPDeviceID
    } | Export-Csv "C:\hdmi-log.csv" -Append -NoTypeInformation
}
Wait-Event   # keeps session alive