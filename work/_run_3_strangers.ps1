$ErrorActionPreference = 'Stop'
$ak = '10dc62485013d5837bde721878129b6a65b95486447c43c4'
$strangers = @('stranger_education','stranger_saas','stranger_logistics')
foreach ($s in $strangers) {
    $url = "http://127.0.0.1:2001/api/v1/video-editor/batches/edit-batch-$s/items/edit-item-$s/local-export"
    try {
        $resp = Invoke-WebRequest -Method Post -Uri $url -Headers @{'X-API-Key' = $ak} -UseBasicParsing -TimeoutSec 600
        Write-Host ("$s : " + $resp.StatusCode)
    } catch {
        Write-Host ("$s : err " + $_.Exception.Response.StatusCode.value__ + " / " + $_.Exception.Message)
    }
    Start-Sleep -Seconds 2
}
Write-Host '---'
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 8
    $allDone = $true
    foreach ($s in $strangers) {
        try {
            $r = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:2001/api/v1/video-editor/batches/edit-batch-$s" -Headers @{'X-API-Key' = $ak} -TimeoutSec 10
            $j = $r.items[0].job.status
            if ($j -ne 'succeeded' -and $j -ne 'failed') {
                $allDone = $false
                Write-Host ("$s : $j")
            } else {
                Write-Host ("$s : $j")
            }
        } catch {
            $allDone = $false
            Write-Host ("$s : err " + $_.Exception.Message)
        }
    }
    if ($allDone) { Write-Host ("all done at " + ($i*8) + "s"); break }
}
