param([switch]$Set)
Add-Type -Path (Join-Path $PSScriptRoot "audio_types.cs") -ErrorAction Stop
$M = [System.Runtime.InteropServices.Marshal]

function GetIface([IntPtr]$pUnk, [type]$t) {
    return $M::GetTypedObjectForIUnknown($pUnk, $t)
}

function GetFriendly([IntPtr]$pDev){
    $dev = GetIface $pDev ([IMMDevice])
    [IntPtr]$pStore = 0
    $dev.OpenPropertyStore(0, [ref]$pStore) | Out-Null
    $store = GetIface $pStore ([IPropertyStore])
    $pk = New-Object PROPERTYKEY
    $pk.fmtid = [Guid]'A45C254E-DF1C-4EFD-8020-67D146A850E0'
    $pk.pid = 14
    [IntPtr]$pv = 0
    $store.GetValue([ref]$pk, [ref]$pv) | Out-Null
    $v = [PROPVARIANT]$M::PtrToStructure($pv, [type][PROPVARIANT])
    if ($v.vt -eq 31) { return $M::PtrToStringUni($v.pwszVal) }
    return ""
}

$enumObj = [System.Activator]::CreateInstance([System.Type]::GetTypeFromCLSID([Guid]"BCDE0395-E52F-467C-8E3D-C4579291692E"))
$pUnkEnum = $M::GetIUnknownForObject($enumObj)
$e = GetIface $pUnkEnum ([IMMDeviceEnumerator])
[IntPtr]$pColl = 0
$e.EnumAudioEndpoints(1, 1, [ref]$pColl) | Out-Null
$coll = GetIface $pColl ([IMMDeviceCollection])
[int]$cnt = 0; $coll.GetCount([ref]$cnt) | Out-Null
Write-Host "=== 录音(捕获)设备 ==="
$cableOutId = $null
for ($i = 0; $i -lt $cnt; $i++) {
    [IntPtr]$pDev = 0; $coll.Item($i, [ref]$pDev) | Out-Null
    $devObj = GetIface $pDev ([IMMDevice])
    [string]$id = ""; $devObj.GetId([ref]$id) | Out-Null
    $name = GetFriendly $pDev
    Write-Host "  [$i] $name"
    if ($name -match 'CABLE Output') { $cableOutId = $id }
}
[IntPtr]$pDef = 0
$e.GetDefaultAudioEndpoint(1, 0, [ref]$pDef) | Out-Null
if ($pDef -ne [IntPtr]::Zero) {
    $d = GetIface $pDef ([IMMDevice])
    [string]$did = ""; $d.GetId([ref]$did) | Out-Null
    Write-Host "=== 当前默认录音设备: $did ==="
}
if ($Set) {
    if ($cableOutId) {
        $pcObj = [System.Activator]::CreateInstance([System.Type]::GetTypeFromCLSID([Guid]"870AF99C-171D-4F9E-AF0D-E63DF40C2BC9"))
        $pUnkPc = $M::GetIUnknownForObject($pcObj)
        $pc = GetIface $pUnkPc ([IPolicyConfig])
        foreach ($role in @(0,1,2)) {
            $pc.SetDefaultEndpoint($cableOutId, $role) | Out-Null
            Write-Host "  [Set] CABLE Output -> 默认录音(role=$role)"
        }
        Write-Host ">>> 完成。请在声音设置确认 CABLE Output 已是默认输入；或直接在 WeType 里选 CABLE Output 作为麦克风。"
    } else {
        Write-Host "!!! 未找到 CABLE Output 设备，无法设置。"
    }
}
