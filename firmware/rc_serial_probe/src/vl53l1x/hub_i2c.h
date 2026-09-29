// A TwoWire-shaped stand-in that speaks to a device behind the PaHub, so the vendored
// Pololu VL53L1X driver can be used UNMODIFIED apart from its bus type.
//
// WHY NOT Wire. The StamPLC's external I2C is an m5::I2C_Class, and every device on it
// sits behind a PCA9548A mux that must be pointed at the right channel before each
// transaction. Arduino's Wire is not in that path at all. Rather than rewrite the eight
// I2C helpers inside a 900-line vendored driver -- and re-do it on every upstream update
// -- this presents the five TwoWire methods that driver actually uses.
//
// Writes are buffered until endTransmission() so the whole register write goes out as ONE
// transaction. That is not just tidiness: this bus has a documented history of dropping
// closely-spaced transactions, and a split register write is exactly the shape that gets
// lost. Reads are buffered by requestFrom() and drained by read().
#pragma once
#include <Arduino.h>
#include <M5Unified.h>

class HubI2C {
public:
    // `select` points the mux at this device's channel; it runs before every transaction
    // because anything else on the bus may have moved the mux since our last one.
    using SelectFn = bool (*)();

    HubI2C(m5::I2C_Class **bus, uint8_t addr, SelectFn select, uint32_t hz)
        : bus_(bus), addr_(addr), select_(select), hz_(hz) {}

    void beginTransmission(uint8_t) { txn_ = 0; }
    size_t write(uint8_t b) { if (txn_ < sizeof(tx_)) tx_[txn_++] = b; return 1; }

    // Returns 0 on success, like TwoWire — the driver stores this in last_status and
    // checks it, so the convention has to be honoured.
    uint8_t endTransmission()
    {
        if (!ready()) return 4;
        bool ok = (*bus_)->start(addr_, false, hz_) && (*bus_)->write(tx_, txn_);
        ok = (*bus_)->stop() && ok;    // STOP on EVERY path, including failure — an
        txn_ = 0;                      // abandoned transaction is what wedges this bus
        return ok ? 0 : 4;
    }

    uint8_t requestFrom(uint8_t, uint8_t n)
    {
        rxn_ = rxi_ = 0;
        if (!ready() || n > sizeof(rx_)) return 0;
        bool ok = (*bus_)->start(addr_, true, hz_) && (*bus_)->read(rx_, n, true);
        (*bus_)->stop();
        if (!ok) return 0;
        rxn_ = n;
        return n;
    }

    // A read past the end returns 0 rather than blocking. The driver reads exactly what it
    // asked for, so this only fires when a transaction failed — and 0 is a value the range
    // status will reject anyway.
    int read() { return (rxi_ < rxn_) ? rx_[rxi_++] : 0; }
    int available() const { return rxn_ - rxi_; }

private:
    bool ready()
    {
        return *bus_ != nullptr && (*bus_)->isEnabled() && (select_ == nullptr || select_());
    }
    m5::I2C_Class **bus_;
    uint8_t         addr_;
    SelectFn        select_;
    uint32_t        hz_;
    uint8_t tx_[8]  = {0};   // longest write the driver makes is reg(2) + 4 data bytes
    uint8_t rx_[24] = {0};   // longest read is the 17-byte results block
    uint8_t txn_ = 0, rxn_ = 0, rxi_ = 0;
};
