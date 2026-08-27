$ErrorActionPreference = 'Stop'
$ak = '10dc62485013d5837bde721878129b6a65b95486447c43c4'

# 4 个 batch: r8 + 3 陌生
$batches = @(
    @{bid='edit-batch-2e9dc62a6c73'; iid='edit-item-bea23c3ea82e'; name='r8'},
    @{bid='edit-batch-stranger_education'; iid='edit-item-stranger_education'; name='stranger_education'},
    @{bid='edit-batch-stranger_saas'; iid='edit-item-stranger_saas'; name='stranger_saas'},
    @{bid='edit-batch-stranger_logistics'; iid='edit-item-stranger_logistics'; name='stranger_logistics'}
)

foreach ($b in $batches) {
    $url = "http://127.0.0.1:2001/api/v1/video-editor/batches/$($b.bid)/items/$($b.iid)/local-export"
    try {
        $resp = Invoke-WebRequest -Method Post -Uri $url -Headers @{'X-API-Key' = $ak} -UseBasicParsing -TimeoutSec 600
        Write-Host ("$($b.name) : " + $resp.StatusCode)
    } catch {
        Write-Host ("$($b.name) : err " + $_.Exception.Response.StatusCode.value__ + " / " + $_.Exception.Message)
    }
    Start-Sleep -Seconds 2
}

Write-Host '---'
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 6
    $allDone = $true
    foreach ($b in $batches) {
        try {
            $r = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:2001/api/v1/video-editor/batches/$($b.bid)" -Headers @{'X-API-Key' = $ak} -TimeoutSec 10
            $j = $r.items[0].job.status
            $etid = $r.items[0].edit_task_id
            if ($j -ne 'succeeded' -and $j -ne 'failed') {
                $allDone = $false
                Write-Host ("$($b.name) [$etid] : $j")
            } else {
                Write-Host ("$($b.name) [$etid] : $j")
            }
        } catch {
            $allDone = $false
            Write-Host ("$($b.name) : err " + $_.Exception.Message)
        }
    }
    if ($allDone) { Write-Host ("all done at " + ($i*6) + "s"); break }
}
