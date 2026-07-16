package dev.pdalink.prototype;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;

public class LocalHotspotAddressSelectorTest {
    @Test
    public void selectsNewSamsungSoftApAddress() {
        LocalHotspotAddressSelector.InterfaceAddress selected = LocalHotspotAddressSelector.select(
                Arrays.asList(address("lo", "127.0.0.1"), address("p2p-wlan0-0", "192.168.49.1")),
                Arrays.asList(
                        address("lo", "127.0.0.1"),
                        address("p2p-wlan0-0", "192.168.49.1"),
                        address("swlan0", "192.168.43.1")));

        assertEquals("swlan0", selected.interfaceName);
        assertEquals("192.168.43.1", selected.address);
    }

    @Test
    public void prefersSoftApInterfaceWhenMultiplePrivateAddressesAppear() {
        LocalHotspotAddressSelector.InterfaceAddress selected = LocalHotspotAddressSelector.select(
                Collections.emptyList(),
                Arrays.asList(
                        address("eth0", "10.20.30.1"),
                        address("ap_br_swlan0", "192.168.137.1")));

        assertEquals("ap_br_swlan0", selected.interfaceName);
    }

    @Test
    public void excludesP2pAndCellularAddresses() {
        LocalHotspotAddressSelector.InterfaceAddress selected = LocalHotspotAddressSelector.select(
                Collections.emptyList(),
                Arrays.asList(
                        address("p2p-wlan0-0", "192.168.49.1"),
                        address("v4-rmnet0", "192.168.0.4")));

        assertNull(selected);
    }

    @Test
    public void derivesSlash24ClientPrefix() {
        assertEquals("192.168.137.", LocalHotspotAddressSelector.clientPrefix("192.168.137.1"));
        assertEquals("", LocalHotspotAddressSelector.clientPrefix("bad-address"));
    }

    private LocalHotspotAddressSelector.InterfaceAddress address(String name, String value) {
        return new LocalHotspotAddressSelector.InterfaceAddress(name, value);
    }
}
