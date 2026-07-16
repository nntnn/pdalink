package dev.pdalink.prototype;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class DebugControlReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null
                || !ProxyActions.ACTION_QUERY_LOCAL_HOTSPOT.equals(intent.getAction())) {
            setResultCode(Activity.RESULT_CANCELED);
            setResultData("unsupported");
            return;
        }

        String snapshot = LocalHotspotDebugState.encodedSnapshot();
        if (snapshot.isEmpty()) {
            setResultCode(Activity.RESULT_CANCELED);
            setResultData("inactive");
            return;
        }

        setResultCode(Activity.RESULT_OK);
        setResultData(snapshot);
    }
}
